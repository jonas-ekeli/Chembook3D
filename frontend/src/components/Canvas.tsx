import {
  applyNodeChanges,
  Background,
  BaseEdge,
  ConnectionMode,
  Controls,
  EdgeLabelRenderer,
  getBezierPath,
  getNodesBounds,
  getViewportForBounds,
  Handle,
  MarkerType,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  useConnection,
  useReactFlow,
  useStore,
  useStoreApi,
  useUpdateNodeInternals,
  type Connection,
  type Edge,
  type EdgeProps,
  type InternalNode,
  type Node as FlowNode,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { createPortal } from 'react-dom'
import { toPng, toSvg } from 'html-to-image'
import {
  createContext,
  memo,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type DragEvent,
  type MouseEvent,
} from 'react'
import {
  FADED_STATUSES,
  formatDelta,
  pathChip,
  speciesChip,
  STATUS_LABEL,
  type Canvas as CanvasData,
  type Group,
  type GroupLayout,
  type Node,
  type Note,
  type NoteLayout,
  type PathOnEdge,
  type SubInvestigation,
  SIDES,
  isSide,
  type Side,
  type Transition,
  type TransitionSides,
} from '../api'
import { MoleculeSketch } from './MoleculeSketch'
import { useTheme } from '../theme'
import { colourToken } from '../util'
import { CardNotes, type NoteActions } from './CanvasNotes'
import { noteTitle } from '../notes'
import { Icon } from '../icons'
import { EmptyState } from './EmptyState'
import { Menu } from './MoreMenu'
import { cardEnergy, minus, type CanvasEnergy, type CardEnergy } from '../cardEnergy'
import { curveOf, LabelPlacer, leaderEnd, pointAt, type Curve, type Rect } from '../labelLayout'
import { useRequest, type Request } from '../commands'
import { chainOf, contentsText, openInside, placeOf, subName, subsOf, visibleCanvases, canvasName } from '../subCanvas'

import type { Filters, Selection, ViewMode } from '../canvasView'
import type { Rotation } from '../chem'

const NO_BRANCH = '#98a2b3'

function speciesChips(t: Transition): TransitionData['species'] {
  return t.species.map((s) => ({
    text: speciesChip(s),
    direction: s.direction,
    title: `${s.label}${s.formula ? ` (${s.formula})` : ''} ${s.direction} here`,
  }))
}
const POSITION: Record<Side, Position> = {
  top: Position.Top,
  right: Position.Right,
  bottom: Position.Bottom,
  left: Position.Left,
}

/** D76: a handle on each side of a box; an arrow can leave from or arrive at any of them. The
 * canvas connects in loose mode, so every handle is both a start and an end. */
function SideHandles() {
  return (
    <>
      {SIDES.map((side) => (
        <Handle key={side} id={side} type="source" position={POSITION[side]} />
      ))}
    </>
  )
}

/** D175: wide enough for the label and the energy in one row at 14 and 15 px. */
const NODE_WIDTH = 188
const MEMBER_CELL = { compact: { w: 208, h: 86 }, energy: { w: 208, h: 86 }, structure: { w: 208, h: 196 } }

export type { CanvasEnergy } from '../cardEnergy'

/** D176: an edge's ΔX, "ΔG" (or "ΔG‡" for a barrier) and the number apart; no number is "n/a"
 * with its reason as a tooltip. */
type EdgeEnergy = { type: string; value: string | null; title: string; barrier: boolean }
/** D175: the energy on a card: a small muted "ΔG", the number bold with a real minus sign. */
function CardEnergyText({ energy, below }: { energy: CardEnergy; below: boolean }) {
  return (
    <span
      className={`cnode-energy${energy.value === null ? ' na' : ''}${energy.missing ? ' note' : ''}${below ? ' below-structure' : ''}`}
      title={energy.title}
    >
      {energy.value === null ? (
        (energy.missing ?? '—')
      ) : (
        <>
          <span className="cnode-energy-type">{energy.type}</span> {minus(energy.value)}
        </>
      )}
    </span>
  )
}
const GROUP_HEADER = 48
/** D193: an expanded sub-investigation's header, the margin round its inside, and the size the
 * cards inside are taken to have when the box is sized round them (they are measured only once
 * drawn; a card dragged past the edge grows the box). */
const SUB_HEADER = 44
const SUB_PAD = 24
const CARD_HEIGHT: Record<ViewMode, number> = { compact: 78, energy: 78, structure: 196 }
const STUB_GAP = { x: 300, y: 84 }

type StructureData = {
  node: Node
  colour: string
  stepName: string | null
  mode: ViewMode
  faded: boolean
  representative: boolean
  energy: CardEnergy | null
  /** The group this node is drawn in place of, when the filters leave it alone (A23). */
  standsFor?: string
  /** D85, D87: the notes on the card, on its corners or floating. */
  notes: Note[]
  noteActions: NoteActions
}

const NO_NOTES: Note[] = []
type GroupData = {
  group: Group
  colour: string
  stepName: string | null
  memberCount: number
  representativeLabel: string | null
  /** The representative's geometry, drawn on the collapsed group in structure mode (D68). */
  representativeXyz: string | null
  /** The representative's saved 3D orientation, so the group draws it as its own card does. */
  representativeRotation: Rotation | null
  mode: ViewMode
  ts: boolean
  expanded: boolean
  onToggle: (id: string) => void
  onLayout: (id: string, layout: GroupLayout) => void
  /** D89: opens the member order list; none in the read-only copy. */
  onOrder?: (id: string) => void
  energy: CardEnergy | null
  /** D85: the notes on its members, shown as a count while it is collapsed. */
  memberNotes: Note[]
  /** D158: each member's warnings, "label: code: message", marked on the group's head. */
  memberWarnings: string[]
}
/** D193: a sub-investigation's box: closed (its face and what it holds), or expanded in place
 * around its inside. Its own canvas is opened with Open or a double-click (A128). */
type SubData = {
  sub: SubInvestigation
  /** The node whose structure the closed box shows, only as a picture (Q59). */
  face: Node | null
  mode: ViewMode
  expanded: boolean
  /** Nodes and transitions inside with something open (the overview's open items). */
  open: number
  onToggle: (id: string) => void
  onOpen?: (id: string) => void
}
/** D193: with a sub-investigation open, a neighbour outside it that a crossing edge joins,
 * faded at the rim; a click goes out to it. */
type StubData = { label: string; where: string; ts: boolean; colour: string }

type TransitionData = {
  /** The transitions this line stands for; more than one between two collapsed groups (A24). */
  ids: string[]
  /** D70: of several, the one joining the groups' representatives, whose ΔX the line shows. */
  representative: string | null
  colour: string
  direct: boolean
  crossBranch: boolean
  status: string
  faded: boolean
  energy: EdgeEnergy | null
  /** D176: of `ids`, those that end at a transition state (a TS node, or a group whose
   * representative is one, D111): their ΔX is a barrier. */
  intoTs: string[]
  /** D69: free species joining ("+ propene") or leaving ("− C₂H₄") on the edge. */
  species: { text: string; direction: string; title: string }[]
  /** W-BALANCE messages, when the atoms or charge do not balance. */
  warnings: string[]
  /** D121: the scan paths run on the edge, and the chip that stands for them. */
  scanPaths: PathOnEdge[]
  paths: { text: string; title: string; missed: boolean } | null
}

/** D121: what clicking an edge's scan path chip does (the edge's id). */
const PathChipClick = createContext<((transitionId: string) => void) | null>(null)

/** D184: what a right-click on the canvas was on: a node card, a group, an arrow, the
 * selection's box, or the empty canvas (with the canvas position under the pointer). */
export type CanvasTarget =
  | { kind: 'node' | 'group' | 'edge' | 'sub'; id: string }
  | { kind: 'selection' }
  | { kind: 'pane'; at: { x: number; y: number } }
/** D184: a right-click at a point of the window on something of the canvas. */
export type CanvasContextMenu = (target: CanvasTarget, point: { x: number; y: number }) => void

const StructureNode = memo(function StructureNode({ data, selected }: NodeProps<FlowNode<StructureData>>) {
  const { node, colour, stepName, mode, faded, representative, energy } = data
  const warnings = node.warnings.map((w) => `${w.code}: ${w.message}`).join('\n')
  // D175: a planned node's card is dashed, so it reads apart from a computed one at a glance.
  const planned = node.status === 'planned'
  return (
    <div
      className={`cnode${selected ? ' selected' : ''}${faded ? ' faded' : ''}${planned ? ' planned' : ''}${representative ? ' representative' : ''}`}
      style={{ ['--branch' as string]: colour }}
      data-testid="canvas-node"
    >
      <SideHandles />
      <div className="cnode-head">
        <span className="cnode-label">{node.label || <em>Untitled node</em>}</span>
        {representative && (
          <span className="rep-mark" title="Representative of its group">
            ★
          </span>
        )}
        {node.warnings.length > 0 && (
          <span className="warn-icon" role="img" aria-label="Has warnings" title={warnings}>
            ⚠
          </span>
        )}
        {node.role === 'transition_state' && (
          <span className="ts-mark" title="Transition state">
            TS
          </span>
        )}
        {energy && mode !== 'structure' && <CardEnergyText energy={energy} below={false} />}
      </div>
      {mode === 'structure' && (
        <MoleculeSketch xyz={node.xyz} rotation={node.view_rotation} width={NODE_WIDTH - 24} height={120} />
      )}
      {energy && mode === 'structure' && <CardEnergyText energy={energy} below />}
      <div className="cnode-meta">
        <span className={`status status-${node.status}`}>{STATUS_LABEL[node.status]}</span>
        {stepName && <span className="cnode-step">{stepName}</span>}
      </div>
      <CardNotes notes={data.notes} actions={data.noteActions} />
    </div>
  )
})

/** What the layout button of an expanded group does next (A21). */
const LAYOUT_BUTTON: Record<GroupLayout, { label: string; icon: string }> = {
  vertical: { label: 'Stack members vertically', icon: '↕' },
  horizontal: { label: 'Line members up horizontally', icon: '↔' },
  grid: { label: 'Arrange members in a grid', icon: '▦' },
}

const GroupBox = memo(function GroupBox({ data, selected }: NodeProps<FlowNode<GroupData>>) {
  const { group, colour, stepName, memberCount, representativeLabel, mode, ts, expanded } = data
  const { representativeXyz, representativeRotation } = data
  const { onToggle, onLayout, onOrder, energy, memberNotes, memberWarnings } = data
  // A21: the button cycles grid (a new group's layout), vertical line, horizontal line, grid.
  const next: GroupLayout =
    group.layout === 'vertical' ? 'horizontal' : group.layout === 'horizontal' ? 'grid' : 'vertical'
  const nextLabel = LAYOUT_BUTTON[next]
  return (
    <div
      className={`cgroup${selected ? ' selected' : ''}${expanded ? ' expanded' : ''}`}
      style={{ ['--branch' as string]: colour }}
      data-testid="canvas-group"
    >
      <SideHandles />
      <div className="cgroup-head">
        <span className="cnode-label">{group.label || <em>Group</em>}</span>
        {ts && (
          <span className="ts-mark" title="Transition states">
            TS
          </span>
        )}
        <span className="muted small">
          {memberCount} member{memberCount === 1 ? '' : 's'}
        </span>
        {memberWarnings.length > 0 && (
          <span
            className="warn-icon"
            role="img"
            aria-label="Members have warnings"
            title={`Warnings on members:\n${memberWarnings.join('\n')}`}
          >
            ⚠
          </span>
        )}
        {!expanded && memberNotes.length > 0 && (
          <span
            className="note-count"
            role="img"
            aria-label={`${memberNotes.length} note${memberNotes.length === 1 ? '' : 's'} on members`}
            title={`Notes on members (expand the group to see them):\n${memberNotes.map(noteTitle).join('\n')}`}
          >
            📌 {memberNotes.length}
          </span>
        )}
        {expanded && memberCount > 1 && (
          <button
            className="small nodrag"
            aria-label={nextLabel.label}
            title={nextLabel.label}
            onClick={(event) => {
              event.stopPropagation()
              onLayout(group.id, next)
            }}
          >
            {nextLabel.icon}
          </button>
        )}
        {expanded && memberCount > 1 && onOrder && (
          <button
            className="small nodrag"
            aria-label="Order members"
            title="Order members"
            onClick={(event) => {
              event.stopPropagation()
              onOrder(group.id)
            }}
          >
            ⇅
          </button>
        )}
        <button
          className="small nodrag"
          aria-label={expanded ? 'Collapse group' : 'Expand group'}
          onClick={(event) => {
            event.stopPropagation()
            onToggle(group.id)
          }}
        >
          {expanded ? '−' : '+'}
        </button>
      </div>
      {!expanded && mode === 'structure' && representativeXyz && (
        <MoleculeSketch xyz={representativeXyz} rotation={representativeRotation} width={NODE_WIDTH - 24} height={120} />
      )}
      {!expanded && energy && <CardEnergyText energy={energy} below={mode === 'structure'} />}
      {!expanded && (
        <div className="cnode-meta">
          <span className="small">
            {representativeLabel ? <>★ {representativeLabel}</> : <em className="muted">no representative</em>}
          </span>
          {stepName && <span className="cnode-step">{stepName}</span>}
        </div>
      )}
    </div>
  )
})

const SubBox = memo(function SubBox({ data, selected }: NodeProps<FlowNode<SubData>>) {
  const { sub, face, mode, expanded, open, onToggle, onOpen } = data
  const name = subName(sub)
  return (
    <div
      className={`csub${selected ? ' selected' : ''}${expanded ? ' expanded' : ''}`}
      data-testid="canvas-sub"
      title={sub.notes || undefined}
    >
      <SideHandles />
      <div className="csub-head">
        <Icon name="layers" />
        <span className="cnode-label">{name}</span>
        <button
          className="small nodrag"
          aria-label={expanded ? `Collapse ${name}` : `Expand ${name} here`}
          title={expanded ? 'Collapse' : 'Expand here: show its inside on this canvas'}
          onClick={(event) => {
            event.stopPropagation()
            onToggle(sub.id)
          }}
        >
          {expanded ? '−' : '+'}
        </button>
        {onOpen && (
          <button
            className="small nodrag"
            aria-label={`Open ${name}`}
            title="Open: switch the canvas to its inside (double-click)"
            onClick={(event) => {
              event.stopPropagation()
              onOpen(sub.id)
            }}
          >
            <Icon name="enter" />
          </button>
        )}
      </div>
      {!expanded && mode === 'structure' && (
        <MoleculeSketch xyz={face?.xyz ?? null} rotation={face?.view_rotation ?? null} width={NODE_WIDTH - 24} height={120} />
      )}
      {!expanded && (
        <div className="cnode-meta">
          <span className="small">{contentsText(sub)}</span>
          {open > 0 && (
            <span className="badge warn" title="Nodes and transitions inside with something open (failed, running, planned, a warning, no TS)">
              {open} open
            </span>
          )}
        </div>
      )}
      {!expanded && face && mode !== 'structure' && (
        <div className="csub-face small muted" title="The structure this box shows (its face)">
          {face.label || 'Untitled node'}
        </div>
      )}
    </div>
  )
})

const StubNode = memo(function StubNode({ data }: NodeProps<FlowNode<StubData>>) {
  return (
    <div className="cstub" style={{ ['--branch' as string]: data.colour }} data-testid="canvas-stub" title={`Go to it, ${data.where}`}>
      <SideHandles />
      <div className="cnode-head">
        <span className="cnode-label">{data.label}</span>
        {data.ts && <span className="ts-mark">TS</span>}
      </div>
      <div className="cnode-meta">
        <span className="small">{data.where}</span>
      </div>
    </div>
  )
})

/**
 * React Flow raises an edge that ends at a node inside a group (an expanded group's member)
 * to that node's level, above the layer where edge labels are drawn. Its label is raised as
 * far, so the energy and the other marks stay on top of the line as on every other edge.
 */
function useEdgeLevel(source: string, target: string): number {
  return useStore((store) => {
    const level = (id: string) => {
      const node = store.nodeLookup.get(id)
      return node?.parentId ? node.internals.z : 0
    }
    return Math.max(level(source), level(target))
  })
}

/** The node cards labels keep clear of: every drawn card, and an expanded group's header. */
function cardRects(nodes: Iterable<InternalNode>): Rect[] {
  const rects: Rect[] = []
  for (const node of nodes) {
    const { width, height } = node.measured
    if (node.hidden || !width || !height) continue
    const { x, y } = node.internals.positionAbsolute
    const open =
      (node.type === 'cgroup' && (node.data as GroupData).expanded) ||
      (node.type === 'csub' && (node.data as SubData).expanded)
    rects.push({ x: Math.round(x), y: Math.round(y), w: Math.round(width), h: Math.round(open ? GROUP_HEADER : height) })
  }
  return rects
}

/**
 * D98: one placer per canvas puts the edge labels clear of one another and of the cards.
 * useStoreApi hands each component its own wrapper of the store, so the placer is kept by the
 * store's getState, which they share.
 */
const placers = new WeakMap<object, LabelPlacer>()
function useLabelPlacer(): LabelPlacer {
  const { getState } = useStoreApi()
  let placer = placers.get(getState)
  if (!placer) {
    placer = new LabelPlacer(() => cardRects(getState().nodeLookup.values()))
    placers.set(getState, placer)
  }
  return placer
}

/** Away from the card, for each side an edge can leave or arrive on. */
const OUTWARD: Record<Position, { x: number; y: number }> = {
  [Position.Left]: { x: -1, y: 0 },
  [Position.Right]: { x: 1, y: 0 },
  [Position.Top]: { x: 0, y: -1 },
  [Position.Bottom]: { x: 0, y: 1 },
}
/** How far apart (px) the two ends may sit across their side and still count as lined up. */
const LINED_UP = 40
/** How far the control points reach out: a share of the ends' distance, kept within these px. */
const BOW = { share: 0.3, min: 36, max: 90 }

/**
 * D147: an edge leaving and arriving on the same side of two cards lined up (members of a group
 * stacked in a column or a row) would be a straight line along the cards' sides, hard to see.
 * It bows out from that side instead, a little more the further apart its ends are.
 */
function bowedOut(sx: number, sy: number, tx: number, ty: number, side: Position): Curve | null {
  const out = OUTWARD[side]
  const along = Math.abs(out.x ? ty - sy : tx - sx)
  // How much further out (along the outward direction) the target end sits than the source end.
  const ahead = out.x ? (tx - sx) * out.x : (ty - sy) * out.y
  if (along < 1 || Math.abs(ahead) > LINED_UP) return null
  const reach = Math.min(BOW.max, Math.max(BOW.min, along * BOW.share))
  // Both control points reach the same distance past the end further out, so the bow clears both cards.
  const fromSource = Math.max(0, ahead) + reach
  const fromTarget = Math.max(0, -ahead) + reach
  return [
    { x: sx, y: sy },
    { x: sx + out.x * fromSource, y: sy + out.y * fromSource },
    { x: tx + out.x * fromTarget, y: ty + out.y * fromTarget },
    { x: tx, y: ty },
  ]
}

/**
 * D176: one pill per arrow: ΔX (amber "ΔG‡" when the arrow ends at a TS), the W-BALANCE warning
 * as a small "!" badge and "no TS" (D53) as an italic tag inside it; its outline is the
 * transition's status (solid done, dashed planned), which is also its tooltip and spoken name.
 * With none of these shown it is a small empty pill, where the status dot used to be.
 */
function EdgePill({ data }: { data: TransitionData }) {
  const { energy, warnings, direct, status } = data
  const empty = !energy && !warnings.length && !direct
  return (
    <span
      className={`edge-pill status-${status}${energy?.barrier ? ' barrier' : ''}${empty ? ' empty' : ''}`}
      title={`Transition: ${STATUS_LABEL[status] ?? status}`}
    >
      {energy && (
        <span className={`edge-energy${energy.value === null ? ' na' : ''}`} title={energy.title}>
          <span className="edge-energy-type">{energy.type}</span> {energy.value ?? 'n/a'}
        </span>
      )}
      {direct && (
        <span className="no-ts" title="Neither end is a transition-state node (D53)">
          no TS
        </span>
      )}
      {warnings.length > 0 && (
        <span className="warn-badge" role="img" aria-label="Does not balance" title={warnings.join('\n')}>
          !
        </span>
      )}
      <span className="visually-hidden">{STATUS_LABEL[status] ?? status}</span>
    </span>
  )
}

function TransitionEdge(props: EdgeProps<Edge<TransitionData>>) {
  const { id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, selected } =
    props
  const level = useEdgeLevel(source, target)
  let [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  const dx = targetX - sourceX
  const sideways = sourcePosition === Position.Right && targetPosition === Position.Left
  const bowed =
    sourcePosition === targetPosition ? bowedOut(sourceX, sourceY, targetX, targetY, sourcePosition) : null
  if (bowed) {
    const [, c1, c2] = bowed
    path = `M ${sourceX},${sourceY} C ${c1.x},${c1.y} ${c2.x},${c2.y} ${targetX},${targetY}`
    const middle = pointAt(bowed, 0.5)
    labelX = middle.x
    labelY = middle.y
  } else if (sideways && Math.abs(dx) > 200 && Math.abs(targetY - sourceY) < 60) {
    // An edge that skips a column (e.g. a direct connection past a TS) arcs over the nodes
    // in between instead of running behind them.
    const lift = Math.min(160, Math.abs(dx) * 0.25)
    const c1 = [sourceX + dx * 0.3, sourceY - lift]
    const c2 = [targetX - dx * 0.3, targetY - lift]
    path = `M ${sourceX},${sourceY} C ${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${targetX},${targetY}`
    labelX = (sourceX + 3 * c1[0] + 3 * c2[0] + targetX) / 8
    labelY = (sourceY + 3 * c1[1] + 3 * c2[1] + targetY) / 8
  }
  const placer = useLabelPlacer()
  const onPaths = useContext(PathChipClick)
  const label = useRef<HTMLDivElement>(null)
  const placement = useSyncExternalStore(placer.subscribe, () => placer.get(id))
  // Hand the line and the label's size to the placer after every render; it places again
  // only when something changed.
  useLayoutEffect(() => {
    const curve = curveOf(path)
    if (!curve || !label.current) return
    placer.set({ id, curve, w: label.current.offsetWidth, h: label.current.offsetHeight })
  })
  useEffect(() => () => placer.remove(id), [placer, id])
  if (!data) return null
  if (placement) {
    labelX = placement.x
    labelY = placement.y
  }
  // A label moved off its line is tied to it by a thin leader.
  const leader = placement && leaderEnd(placement.anchor, placement, placement.w, placement.h)
  // D53: a direct connection is dotted with a "no TS" marker; P9: between branches dashed.
  const dash = data.direct ? '2 5' : data.crossBranch ? '9 5' : undefined
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: data.colour,
          strokeWidth: selected ? 3 : 2,
          strokeDasharray: dash,
          opacity: data.faded ? 0.35 : 1,
        }}
      />
      {placement && leader && (
        <path
          className="edge-leader"
          d={`M ${placement.anchor.x},${placement.anchor.y} L ${leader.x},${leader.y}`}
          style={{ stroke: data.colour, opacity: data.faded ? 0.35 : 0.8 }}
        />
      )}
      <EdgeLabelRenderer>
        <div
          ref={label}
          className={`edge-label${data.faded ? ' faded' : ''}`}
          data-edge={id}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, zIndex: level || undefined }}
        >
          <EdgePill data={data} />
          {data.ids.length > 1 && (
            <span className="edge-count" title="Parallel transitions between these two boxes; filter by branch to see each">
              {data.ids.length} edges
            </span>
          )}
          {data.species.map((s) => (
            <span key={s.text} className={`chip species-${s.direction}`} title={s.title}>
              {s.text}
            </span>
          ))}
          {data.paths && (
            <button
              className={`chip scan-path-chip nodrag nopan${data.paths.missed ? ' missed' : ''}`}
              title={data.paths.title}
              onClick={(event) => {
                event.stopPropagation()
                onPaths?.(data.ids[0])
              }}
            >
              {data.paths.missed && (
                <span role="img" aria-label="Did not reach end">
                  ⚠{' '}
                </span>
              )}
              {data.paths.text}
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

const nodeTypes = { structure: StructureNode, cgroup: GroupBox, csub: SubBox, stub: StubNode }
const edgeTypes = { transition: TransitionEdge }

/** The sides a new or moved connection uses (D76); a handle without one is left to the default. */
function sides(connection: Connection): TransitionSides {
  return {
    ...(isSide(connection.sourceHandle) ? { source_side: connection.sourceHandle } : {}),
    ...(isSide(connection.targetHandle) ? { target_side: connection.targetHandle } : {}),
  }
}

/** What a click or right-click on a drawn box selects. */
function kindOf(node: FlowNode): 'node' | 'group' | 'sub' {
  return node.type === 'cgroup' ? 'group' : node.type === 'csub' ? 'sub' : 'node'
}

function download(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
}

function CanvasView({
  data,
  mode,
  energy,
  filters,
  selection,
  multi,
  focus,
  expanded,
  onToggleGroup,
  onGroupLayout,
  onGroupOrder,
  onSelect,
  onMultiSelect,
  onToggleNode,
  onConnect,
  onMoveEnds,
  onAddNode,
  onDropFiles,
  onPositions,
  onError,
  onOpenNote,
  onLayoutNote,
  onOpenPaths,
  onImport,
  readOnly = false,
  tools,
  onContextMenu,
  asked,
  canvasId = null,
  onOpenSub,
  onOpenRecord,
}: {
  data: CanvasData
  mode: ViewMode
  energy: CanvasEnergy
  filters: Filters
  selection: Selection
  multi: string[]
  focus: { id: string; n: number } | null
  expanded: Set<string>
  onToggleGroup: (id: string) => void
  onGroupLayout: (id: string, layout: GroupLayout) => void
  onGroupOrder?: (id: string) => void
  onSelect: (selection: Selection) => void
  onMultiSelect: (nodeIds: string[]) => void
  onToggleNode: (id: string) => void
  onConnect: (source: string, target: string, sides: TransitionSides) => void
  onMoveEnds: (transitionIds: string[], sides: TransitionSides) => void
  onAddNode: (position: { x: number; y: number }) => void
  onDropFiles: (files: File[], targetId: string | null, position: { x: number; y: number }) => void
  onPositions: (positions: Record<string, { x: number; y: number }>) => void
  onError: (message: string) => void
  /** D85: edit a note (double-click), and (D87) save how it is drawn: collapsed, its size,
   * where it floats. */
  onOpenNote?: (noteId: string) => void
  onLayoutNote?: (noteId: string, layout: Partial<NoteLayout>) => void
  /** D121: a click on an edge's scan path chip; by default it selects the edge. */
  onOpenPaths?: (transitionId: string) => void
  /** D182: the empty canvas's "Import output files…", the outline's Import. */
  onImport?: () => void
  /** The shared read-only copy (D79): nothing can be moved, connected, added or dropped. */
  readOnly?: boolean
  /** D180: where Fit and Export go (the app's canvas toolbar); null until it is there. Without
   * it they float in the canvas's top-right corner (the read-only copy). */
  tools?: HTMLElement | null
  /** D184: right-click menus; without it (the read-only copy) the browser's own menu shows. */
  onContextMenu?: CanvasContextMenu
  /** D184: "fit" (the selection, else everything) or "fit-all", from a menu, the palette or F. */
  asked?: Request | null
  /** D193: the sub-investigation whose canvas is open; null for the main canvas. */
  canvasId?: string | null
  /** D193: open a box's canvas (its Open button, a double-click). */
  onOpenSub?: (id: string) => void
  /** D193: a rim stub's record, outside the open canvas: go out to it. */
  onOpenRecord?: (id: string) => void
}) {
  const flow = useReactFlow()
  const theme = useTheme() // D185: React Flow's own colours follow the theme
  const updateNodeInternals = useUpdateNodeInternals()
  const store = useStoreApi()
  const placer = useLabelPlacer()
  // Cards that move, appear or are measured can free or block a label's spot.
  useEffect(() => {
    const unsubscribe = store.subscribe(() => placer.cardsChanged())
    return () => {
      unsubscribe()
      placer.dispose()
    }
  }, [store, placer])
  const pathChipClick = useCallback(
    (id: string) => (onOpenPaths ? onOpenPaths(id) : onSelect({ kind: 'edge', id })),
    [onOpenPaths, onSelect],
  )
  const wrapper = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  // D175: handles show only on a hovered or selected card, and on every card while an arrow is drawn.
  const connecting = useConnection((connection) => connection.inProgress)

  const colours = useMemo(() => new Map(data.branches.map((b) => [b.id, b.colour])), [data.branches])
  // D85: one list per node, kept while the notes do not change, so a collapsed note stays so.
  const notesByNode = useMemo(() => {
    const found = new Map<string, Note[]>()
    for (const note of data.notes ?? []) found.set(note.node_id, [...(found.get(note.node_id) ?? []), note])
    return found
  }, [data.notes])
  const noteActions: NoteActions = useMemo(
    () => ({ onOpen: readOnly ? undefined : onOpenNote, onLayout: readOnly ? undefined : onLayoutNote }),
    [readOnly, onOpenNote, onLayoutNote],
  )
  const stepNames = useMemo(() => new Map(data.steps.map((s) => [s.id, s.name || 'Unnamed step'])), [data.steps])

  // Nodes show energies only in energy mode, and in structure mode once a reference is chosen
  // (D73, ΔX below the structure), so only then do energy changes rebuild them. Rebuilding
  // every node when the energy view arrives, just after the canvas loaded, left React Flow
  // re-measuring nodes and occasionally dropping edges or a click.
  const nodeEnergySource =
    mode === 'energy' || (mode === 'structure' && energy.referenceId) ? energy : null

  const { flowNodes, flowEdges: plainEdges, origins } = useMemo(() => {
    // D43, D72: ΔX from the reference node (`cardEnergy`).
    const nodeEnergy = (id: string) => cardEnergy(nodeEnergySource, id)
    const hidden = {
      branches: new Set(filters.branches),
      statuses: new Set(filters.statuses),
      steps: new Set(filters.steps),
    }
    const nodeShown = (n: Node) =>
      !hidden.branches.has(n.branch_id ?? 'none') &&
      !hidden.statuses.has(n.status) &&
      !hidden.steps.has(n.step_id ?? 'none')
    const groupShown = (g: Group) => {
      const branches = [...g.incoming_branch_ids, ...(g.outgoing_branch_id ? [g.outgoing_branch_id] : [])]
      const branchOk = branches.length ? branches.some((b) => !hidden.branches.has(b)) : !hidden.branches.has('none')
      return branchOk && !hidden.steps.has(g.step_id ?? 'none')
    }
    const byId = new Map(data.nodes.map((n) => [n.id, n]))
    const notesOf = (id: string) => notesByNode.get(id) ?? NO_NOTES
    const cell = MEMBER_CELL[mode]
    const chosen = new Set(multi)
    const isSelected = (id: string) => chosen.has(id) || (selection?.kind === 'node' && selection.id === id)

    const result: FlowNode[] = []
    // Where each record is drawn: itself, its collapsed group, the closed box holding it
    // (D193), or a stub at the rim when it is outside the open sub-investigation.
    const drawnAs = new Map<string, string>()
    const branchColour = (id: string | null) => (id ? (colours.get(id) ?? NO_BRANCH) : NO_BRANCH)

    // D193: the boxes. One the filters leave nothing in is hidden (A128); an empty one stays.
    const subs = subsOf(data)
    const holding = new Map<string, { any: boolean; shown: boolean }>()
    for (const node of data.nodes) {
      if (!node.canvas_id) continue
      for (const box of chainOf(subs, node.canvas_id)) {
        const found = holding.get(box.id) ?? { any: false, shown: false }
        found.any = true
        found.shown ||= nodeShown(node)
        holding.set(box.id, found)
      }
    }
    const boxShown = (sub: SubInvestigation) => {
      const found = holding.get(sub.id)
      return !found?.any || found.shown
    }
    const shownSubs = subs.filter((sub) => chainOf(subs, sub.id).every(boxShown))
    const visible = visibleCanvases(shownSubs, canvasId, expanded)
    const drawnBoxes = new Set(
      shownSubs.filter((sub) => visible.has(sub.parent_id)).map((sub) => sub.id),
    )
    // Records outside the open canvas that an edge may need as a stub: their names.
    const outside = new Map<string, { label: string; canvas: string | null; ts: boolean; colour: string }>()
    /** Where a record on canvas `on` goes: its own card, or false once noted as drawn by a
     * box, as outside, or hidden with its box. */
    const own = (on: string | null | undefined, ids: { id: string; label: string; ts: boolean; colour: string }[]) => {
      const place = placeOf(subs, visible, on ?? null)
      if (place.kind === 'self') return true
      for (const record of ids)
        if (place.kind === 'box' && drawnBoxes.has(place.id)) drawnAs.set(record.id, place.id)
        else if (place.kind === 'outside')
          outside.set(record.id, { label: record.label, canvas: on ?? null, ts: record.ts, colour: record.colour })
      return false
    }

    // D193: an expanded box frames its inside at the inside's own layout: each card keeps its
    // place relative to the others, the box sized round them, innermost boxes first.
    const sizes = new Map<string, { w: number; h: number }>()
    const origins = new Map<string, { x: number; y: number }>()
    const cardHeight = CARD_HEIGHT[mode]
    const groupSize = (group: Group) => {
      if (!expanded.has(group.id)) return { w: NODE_WIDTH + 4, h: cardHeight }
      const count = Math.max(1, group.member_ids.length)
      const columns =
        group.layout === 'vertical' ? 1 : group.layout === 'horizontal' ? count : Math.ceil(Math.sqrt(count))
      return { w: columns * cell.w + 16, h: GROUP_HEADER + Math.ceil(count / columns) * cell.h + 8 }
    }
    const openBoxes = shownSubs
      .filter((sub) => drawnBoxes.has(sub.id) && visible.has(sub.id))
      .sort((a, b) => chainOf(subs, b.id).length - chainOf(subs, a.id).length)
    for (const box of openBoxes) {
      const items = [
        ...data.nodes
          .filter((n) => (n.canvas_id ?? null) === box.id && !n.group_id && !n.on_edge && nodeShown(n))
          .map((n) => ({ x: n.pos_x, y: n.pos_y, w: NODE_WIDTH, h: cardHeight })),
        ...data.groups
          .filter((g) => (g.canvas_id ?? null) === box.id)
          .map((g) => ({ x: g.pos_x, y: g.pos_y, ...groupSize(g) })),
        ...shownSubs
          .filter((sub) => sub.parent_id === box.id)
          .map((sub) => ({ x: sub.pos_x, y: sub.pos_y, ...(sizes.get(sub.id) ?? { w: NODE_WIDTH, h: cardHeight + 20 }) })),
      ]
      if (!items.length) {
        origins.set(box.id, { x: 0, y: 0 })
        sizes.set(box.id, { w: NODE_WIDTH + 2 * SUB_PAD, h: SUB_HEADER + SUB_PAD + 40 })
        continue
      }
      const x0 = Math.min(...items.map((i) => i.x))
      const y0 = Math.min(...items.map((i) => i.y))
      const x1 = Math.max(...items.map((i) => i.x + i.w))
      const y1 = Math.max(...items.map((i) => i.y + i.h))
      origins.set(box.id, { x: x0 - SUB_PAD, y: y0 - SUB_HEADER })
      sizes.set(box.id, { w: Math.max(240, x1 - x0 + 2 * SUB_PAD), h: y1 - y0 + SUB_HEADER + SUB_PAD })
    }
    /** A card on canvas `on`: at its own place on the open canvas, or inside its expanded box. */
    const at = (on: string | null | undefined, x: number, y: number) => {
      const box = on ?? null
      if (box === canvasId || box === null) return { position: { x, y } }
      const origin = origins.get(box) ?? { x: 0, y: 0 }
      return { position: { x: x - origin.x, y: y - origin.y }, parentId: box, expandParent: true }
    }
    const faces = new Map(data.nodes.map((n) => [n.id, n]))
    // Outer boxes first: React Flow needs a parent before the cards in it.
    for (const sub of shownSubs
      .filter((s) => drawnBoxes.has(s.id))
      .sort((a, b) => chainOf(subs, a.id).length - chainOf(subs, b.id).length)) {
      const open = visible.has(sub.id)
      drawnAs.set(sub.id, sub.id)
      result.push({
        id: sub.id,
        type: 'csub',
        ...at(sub.parent_id, sub.pos_x, sub.pos_y),
        ariaLabel: `Sub-investigation ${subName(sub)}`,
        selected: chosen.has(sub.id) || (selection?.kind === 'sub' && selection.id === sub.id),
        style: open ? { width: sizes.get(sub.id)?.w, height: sizes.get(sub.id)?.h } : undefined,
        data: {
          sub,
          face: sub.face_id ? (faces.get(sub.face_id) ?? null) : null,
          mode,
          expanded: open,
          open: openInside(data, sub.id),
          onToggle: onToggleGroup,
          onOpen: onOpenSub,
        } satisfies SubData,
      })
    }

    for (const group of data.groups) {
      const members = group.member_ids.map((id) => byId.get(id)).filter((n): n is Node => !!n)
      // D66: members keep their branch, so the filters apply to each member. A group with
      // none left is hidden; one the filters leave with a single member is drawn as that
      // node, in the group's place (A23).
      const shown = members.filter(nodeShown)
      if (hidden.steps.has(group.step_id ?? 'none')) continue
      const groupColour = branchColour(group.outgoing_branch_id ?? shown[0]?.branch_id ?? null)
      const records = [
        { id: group.id, label: group.label || 'Group', ts: false, colour: groupColour },
        ...shown.map((m) => ({
          id: m.id,
          label: m.label || 'Untitled node',
          ts: m.role === 'transition_state',
          colour: branchColour(m.branch_id),
        })),
      ]
      if (!own(group.canvas_id, records)) continue
      // A reconnection stays while its outgoing branch is shown, as the start of that branch.
      const starts = group.outgoing_branch_id !== null && !hidden.branches.has(group.outgoing_branch_id)
      if (members.length ? shown.length === 0 && !starts : !groupShown(group)) continue
      if (shown.length === 1 && members.length > 1) {
        const [only] = shown
        drawnAs.set(group.id, only.id)
        drawnAs.set(only.id, only.id)
        result.push({
          id: only.id,
          type: 'structure',
          className: notesOf(only.id).length ? 'has-notes' : undefined,
          // D87: its notes, floating ones too, are drawn over the cards around it.
          zIndex: notesOf(only.id).length ? 1 : undefined,
          ...at(group.canvas_id, group.pos_x, group.pos_y),
          ariaLabel: `Node ${only.label || 'Untitled node'}`,
          selected: isSelected(only.id),
          data: {
            node: only,
            colour: branchColour(only.branch_id),
            stepName: only.step_id ? (stepNames.get(only.step_id) ?? null) : null,
            mode,
            faded: FADED_STATUSES.has(only.status),
            representative: false,
            energy: nodeEnergy(only.id),
            standsFor: group.id,
            notes: notesOf(only.id),
            noteActions,
          } satisfies StructureData,
        })
        continue
      }
      const open = expanded.has(group.id)
      // A21: a grid (the default), one column or one row.
      const count = Math.max(1, shown.length)
      const columns =
        group.layout === 'vertical' ? 1 : group.layout === 'horizontal' ? count : Math.ceil(Math.sqrt(count))
      const rows = Math.ceil(count / columns)
      const representative = group.representative_id ? byId.get(group.representative_id) : undefined
      // The outgoing branch's colour, or the members' when they share one branch.
      const memberBranches = new Set(shown.map((m) => m.branch_id))
      const colour = group.outgoing_branch_id
        ? branchColour(group.outgoing_branch_id)
        : memberBranches.size === 1
          ? branchColour(shown[0].branch_id)
          : NO_BRANCH
      result.push({
        id: group.id,
        type: 'cgroup',
        ...at(group.canvas_id, group.pos_x, group.pos_y),
        ariaLabel: `Group ${group.label || ''}`.trim(),
        selected: chosen.has(group.id) || (selection?.kind === 'group' && selection.id === group.id),
        style: open ? { width: columns * cell.w + 16, height: GROUP_HEADER + rows * cell.h + 8 } : undefined,
        data: {
          group,
          colour,
          stepName: group.step_id ? (stepNames.get(group.step_id) ?? null) : null,
          memberCount: members.length,
          representativeLabel: representative ? representative.label || 'Untitled node' : null,
          representativeXyz: representative?.xyz ?? null,
          representativeRotation: representative?.view_rotation ?? null,
          mode,
          // A25: a group of transition states is marked like one.
          ts: members.length > 0 && members.every((m) => m.role === 'transition_state'),
          expanded: open,
          onToggle: onToggleGroup,
          onLayout: onGroupLayout,
          onOrder: onGroupOrder,
          energy: nodeEnergy(group.id),
          memberNotes: members.flatMap((m) => notesOf(m.id)),
          memberWarnings: members.flatMap((m) =>
            m.warnings.map((w) => `${m.label || 'Untitled node'}: ${w.code}: ${w.message}`),
          ),
        } satisfies GroupData,
      })
      drawnAs.set(group.id, group.id)
      shown.forEach((member, index) => {
        if (!open) {
          drawnAs.set(member.id, group.id)
          return
        }
        drawnAs.set(member.id, member.id)
        result.push({
          id: member.id,
          type: 'structure',
          className: notesOf(member.id).length ? 'has-notes' : undefined,
          // D87: its notes, floating ones too, are drawn over the cards around it.
          zIndex: notesOf(member.id).length ? 1 : undefined,
          parentId: group.id,
          extent: 'parent',
          draggable: false,
          position: { x: 8 + (index % columns) * cell.w, y: GROUP_HEADER + Math.floor(index / columns) * cell.h },
          ariaLabel: `Node ${member.label || 'Untitled node'}`,
          selected: isSelected(member.id),
          data: {
            node: member,
            colour: branchColour(member.branch_id),
            stepName: member.step_id ? (stepNames.get(member.step_id) ?? null) : null,
            mode,
            faded: FADED_STATUSES.has(member.status),
            representative: group.representative_id === member.id,
            energy: nodeEnergy(member.id),
            notes: notesOf(member.id),
            noteActions,
          } satisfies StructureData,
        })
      })
    }

    for (const node of data.nodes) {
      // D121: a scan path shown on its edge only is drawn as the edge's chip.
      if (node.group_id || !nodeShown(node) || node.on_edge) continue
      const self = { id: node.id, label: node.label || 'Untitled node', ts: node.role === 'transition_state' }
      if (!own(node.canvas_id, [{ ...self, colour: branchColour(node.branch_id) }])) continue
      drawnAs.set(node.id, node.id)
      result.push({
        id: node.id,
        type: 'structure',
        className: notesOf(node.id).length ? 'has-notes' : undefined,
        // D87: its notes, floating ones too, are drawn over the cards around it.
        zIndex: notesOf(node.id).length ? 1 : undefined,
        ...at(node.canvas_id, node.pos_x, node.pos_y),
        ariaLabel: `Node ${node.label || 'Untitled node'}`,
        selected: isSelected(node.id),
        data: {
          node,
          colour: branchColour(node.branch_id),
          stepName: node.step_id ? (stepNames.get(node.step_id) ?? null) : null,
          mode,
          faded: FADED_STATUSES.has(node.status),
          representative: false,
          energy: nodeEnergy(node.id),
          notes: notesOf(node.id),
          noteActions,
        } satisfies StructureData,
      })
    }

    // D193: a neighbour outside the open sub-investigation that a crossing edge joins is a faded
    // stub at the rim: left of everything for an edge coming in, right for one going out.
    if (outside.size) {
      const top = result.filter((n) => !n.parentId)
      const width = (n: FlowNode) => (typeof n.style?.width === 'number' ? n.style.width : NODE_WIDTH)
      const x0 = top.length ? Math.min(...top.map((n) => n.position.x)) : 0
      const x1 = top.length ? Math.max(...top.map((n) => n.position.x + width(n))) : NODE_WIDTH
      const y0 = top.length ? Math.min(...top.map((n) => n.position.y)) : 0
      const sides = new Map<string, 'left' | 'right'>()
      for (const t of data.transitions) {
        if (hidden.statuses.has(t.status)) continue
        if (outside.has(t.source_id) && drawnAs.has(t.target_id)) sides.set(t.source_id, 'left')
        else if (outside.has(t.target_id) && drawnAs.has(t.source_id) && !sides.has(t.target_id))
          sides.set(t.target_id, 'right')
      }
      const rows = { left: 0, right: 0 }
      for (const [id, side] of sides) {
        const record = outside.get(id)!
        const stub = `stub:${id}`
        drawnAs.set(id, stub)
        result.push({
          id: stub,
          type: 'stub',
          position: { x: side === 'left' ? x0 - STUB_GAP.x : x1 + STUB_GAP.x - NODE_WIDTH, y: y0 + rows[side]++ * STUB_GAP.y },
          draggable: false,
          connectable: false,
          ariaLabel: `Outside: ${record.label}`,
          data: {
            label: record.label,
            where: `in ${canvasName(subs, record.canvas)}`,
            ts: record.ts,
            colour: record.colour,
          } satisfies StubData,
        })
      }
    }

    const groupsById = new Map(data.groups.map((g) => [g.id, g]))
    const edges: Edge<TransitionData>[] = []
    // A24: edges joining the same two drawn boxes (e.g. one per conformer between two
    // collapsed groups) are drawn as one line.
    const between = new Map<string, Edge<TransitionData>>()
    // D176: the role the app knows, not where the card sits: a TS node, or a group standing
    // for a TS representative (D111), as the "no TS" rule (D53) counts them.
    const isTs = (id: string) => {
      const node = byId.get(id)
      if (node) return node.role === 'transition_state'
      const representative = groupsById.get(id)?.representative_id
      return !!representative && byId.get(representative)?.role === 'transition_state'
    }
    for (const t of data.transitions) {
      if (hidden.statuses.has(t.status)) continue
      const source = drawnAs.get(t.source_id)
      const target = drawnAs.get(t.target_id)
      if (!source || !target || source === target) continue
      // An edge takes its source's branch colour; from a group, its outgoing branch.
      const sourceNode = byId.get(t.source_id)
      const colour = sourceNode
        ? branchColour(sourceNode.branch_id ?? byId.get(t.target_id)?.branch_id ?? null)
        : branchColour(groupsById.get(t.source_id)?.outgoing_branch_id ?? null)
      const selected = selection?.kind === 'edge' && selection.id === t.id
      const same = between.get(`${source}>${target}`)
      if (same?.data) {
        const merged = same.data
        merged.ids.push(t.id)
        if (isTs(t.target_id)) merged.intoTs.push(t.id)
        merged.colour = merged.colour === colour ? colour : NO_BRANCH
        merged.direct &&= t.direct
        merged.crossBranch &&= t.cross_branch
        merged.faded &&= FADED_STATUSES.has(t.status)
        for (const s of speciesChips(t)) if (!merged.species.some((x) => x.text === s.text)) merged.species.push(s)
        merged.scanPaths.push(...(t.scan_paths ?? []))
        merged.warnings.push(...t.warnings.map((w) => `${w.code}: ${w.message}`))
        same.selected ||= selected
        same.ariaLabel = `${merged.ids.length} transitions`
        same.markerEnd = { type: MarkerType.ArrowClosed, color: merged.colour, width: 16, height: 16 }
        continue
      }
      const drawn: Edge<TransitionData> = {
        id: t.id,
        source,
        target,
        // D76, A24: a line standing for several transitions uses the first one's sides.
        sourceHandle: t.source_side,
        targetHandle: t.target_side,
        type: 'transition',
        ariaLabel: `Transition${t.direct ? ' (no TS)' : ''}`,
        selected,
        markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 16, height: 16 },
        data: {
          ids: [t.id],
          representative: null,
          colour,
          direct: t.direct,
          crossBranch: t.cross_branch,
          status: t.status,
          faded: FADED_STATUSES.has(t.status),
          energy: null,
          intoTs: isTs(t.target_id) ? [t.id] : [],
          species: speciesChips(t),
          warnings: t.warnings.map((w) => `${w.code}: ${w.message}`),
          scanPaths: [...(t.scan_paths ?? [])],
          paths: null,
        },
      }
      between.set(`${source}>${target}`, drawn)
      edges.push(drawn)
    }
    // D70: a line standing for several transitions shows the energy of the one between the
    // representatives: at each end that is a collapsed group, the transition must start or
    // end at the group's representative (or at the group itself). None such: no energy.
    const transitionsById = new Map(data.transitions.map((t) => [t.id, t]))
    const atRepresentative = (end: string, box: string) => {
      // D193: a sub-investigation's box has no energy (Q59), so neither has a line it merges.
      if (subs.some((sub) => sub.id === box) || box.startsWith('stub:')) return false
      const group = groupsById.get(box)
      return !group || end === box || (group.representative_id !== null && group.representative_id === end)
    }
    for (const e of edges) {
      if (!e.data || e.data.ids.length < 2) continue
      e.data.representative =
        e.data.ids.find((id) => {
          const t = transitionsById.get(id)
          return !!t && atRepresentative(t.source_id, e.source) && atRepresentative(t.target_id, e.target)
        }) ?? null
    }
    return { flowNodes: result, flowEdges: edges, origins }
  }, [data, mode, nodeEnergySource, filters, expanded, selection, multi, colours, stepNames, onToggleGroup, onGroupLayout, onGroupOrder, notesByNode, noteActions, canvasId, onOpenSub])

  // FR-EN-02: ΔX = X(target) − X(source) on each edge, or n/a (EN-3). D121: the scan path
  // chips, in the energy unit, are always shown (A68) and never feed ΔX.
  const flowEdges = useMemo(() => {
    const view = energy.view
    const showEnergy = energy.showEdges && !!view
    if (!showEnergy && !plainEdges.some((e) => e.data?.scanPaths.length)) return plainEdges
    const chip = (d: TransitionData) => (d.scanPaths.length ? pathChip(d.scanPaths, energy.settings) : null)
    const edgeEnergy = ({ ids, representative, intoTs }: TransitionData): EdgeEnergy | null => {
      if (!view) return null
      // A24, D70: a line standing for several edges shows the representatives' edge, if any.
      const id = ids.length === 1 ? ids[0] : representative
      const found = id ? view.edges[id] : undefined
      if (!id || !found) return null
      // D176: ΔX into a transition state is that step's barrier, ΔG‡.
      const barrier = intoTs.includes(id)
      const type = `Δ${energy.type}${barrier ? '‡' : ''}`
      const note =
        (barrier ? ', the barrier into a transition state' : '') +
        (ids.length > 1 ? ' (the transition between the representatives)' : '')
      return found.delta === null
        ? {
            type,
            value: null,
            title: (found.message ?? 'one end has no value at this level and type') + note,
            barrier,
          }
        : {
            type,
            // D175, D176: a real minus sign, as on the cards.
            value: formatDelta(found.delta, energy.settings).replace(/^-/, '−'),
            title: (energy.settings?.energy_unit ?? 'kcal/mol') + note,
            barrier,
          }
    }
    return plainEdges.map((e) => ({
      ...e,
      data: { ...e.data!, energy: showEnergy ? edgeEnergy(e.data!) : null, paths: chip(e.data!) },
    }))
  }, [plainEdges, energy])

  // Local copy so dragging is smooth; replaced whenever the records change.
  const [nodes, setNodes] = useState<FlowNode[]>(flowNodes)
  const [shown, setShown] = useState(flowNodes)
  if (shown !== flowNodes) {
    setShown(flowNodes)
    // Keep each node's measured size: React Flow hides a node until it has one, and a node
    // whose size did not change is never measured again.
    setNodes((current) => {
      const measured = new Map(current.map((n) => [n.id, n.measured]))
      return flowNodes.map((n) => (measured.get(n.id) ? { ...n, measured: measured.get(n.id) } : n))
    })
  }
  // React Flow draws an edge only once both of its nodes have measured handles. A node that
  // was replaced before its first measurement arrived can be left without them, and since its
  // size does not change it is never measured again, so its edges stay hidden. Measure any
  // such node once more after each change.
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      const stale = nodes
        .map((n) => flow.getInternalNode(n.id))
        .filter((n) => n && !n.hidden && !n.internals.handleBounds)
        .map((n) => n!.id)
      if (stale.length) updateNodeInternals(stale)
    })
    return () => cancelAnimationFrame(frame)
  }, [nodes, flow, updateNodeInternals])

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((current) => applyNodeChanges(changes, current)),
    [],
  )

  useEffect(() => {
    if (!focus) return
    const internal = flow.getInternalNode(focus.id)
    if (!internal) return
    const { x, y } = internal.internals.positionAbsolute
    const width = internal.measured.width ?? NODE_WIDTH
    const height = internal.measured.height ?? 60
    flow.setCenter(x + width / 2, y + height / 2, { zoom: Math.max(flow.getZoom(), 1), duration: 300 })
  }, [focus, flow])

  const position = (event: { clientX: number; clientY: number }) =>
    flow.screenToFlowPosition({ x: event.clientX, y: event.clientY })

  // The Controls' fit (everything), and the toolbar's Fit (the selection).
  const fitAll = () => flow.fitView({ duration: 300, maxZoom: 1.2, padding: 0.15 })
  const fitSelection = () => flow.fitView({ nodes: flow.getNodes().filter((n) => n.selected), duration: 300, maxZoom: 1.5 })
  useRequest(asked, 'canvas', (action) => {
    if (action.startsWith('fit:'))
      void flow.fitView({ nodes: action.slice(4).split(',').map((id) => ({ id })), duration: 300, maxZoom: 1.5 })
    else if (action === 'fit-all' || (action === 'fit' && !flow.getNodes().some((n) => n.selected))) void fitAll()
    else if (action === 'fit') void fitSelection()
  })
  const menuAt = (event: { clientX: number; clientY: number; preventDefault: () => void }, target: CanvasTarget) => {
    if (!onContextMenu) return
    event.preventDefault()
    onContextMenu(target, { x: event.clientX, y: event.clientY })
  }

  const exportImage = async (what: 'viewport' | 'full', format: 'png' | 'svg') => {
    const viewport = wrapper.current?.querySelector<HTMLElement>('.react-flow__viewport')
    if (!viewport || !wrapper.current) return
    let width = wrapper.current.clientWidth
    let height = wrapper.current.clientHeight
    let transform = flow.getViewport()
    if (what === 'full') {
      const bounds = getNodesBounds(flow.getNodes())
      width = Math.min(8000, Math.max(400, Math.ceil(bounds.width * 1.1)))
      height = Math.min(8000, Math.max(300, Math.ceil(bounds.height * 1.1)))
      transform = getViewportForBounds(bounds, width, height, 0.1, 2, 0.05)
    }
    const options = {
      backgroundColor: colourToken('--bg'), // D185: as the canvas is shown
      width,
      height,
      pixelRatio: format === 'png' ? 2 : 1,
      style: {
        width: `${width}px`,
        height: `${height}px`,
        transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.zoom})`,
      },
    }
    try {
      const url = format === 'png' ? await toPng(viewport, options) : await toSvg(viewport, options)
      download(url, `canvas-${what}.${format}`)
    } catch (err) {
      onError(`Could not export the image: ${String(err)}`)
    }
  }

  // UI5 (D174): icon and short label, the full name in the tooltip. D180: in the app they sit in
  // the canvas toolbar above, drawn there through a portal so they keep this canvas's view.
  const toolClass = tools === undefined ? undefined : 'quiet'
  const canvasTools = (
    <>
      <button
        className={toolClass}
        title="Fit to selection"
        aria-label="Fit to selection"
        onClick={() => void fitSelection()}
      >
        <Icon name="fit" />
        <span className="tb-label">Fit</span>
      </button>
      <Menu
        label="Export image"
        buttonClass={toolClass}
        buttonLabel="Export image ▾"
        title="Export image"
        items={[
          { label: 'Visible area as PNG', onSelect: () => exportImage('viewport', 'png') },
          { label: 'Whole canvas as PNG', onSelect: () => exportImage('full', 'png') },
          { label: 'Visible area as SVG', onSelect: () => exportImage('viewport', 'svg') },
          { label: 'Whole canvas as SVG', onSelect: () => exportImage('full', 'svg') },
        ]}
      >
        <Icon name="download" />
        <span className="tb-label">Export</span>
        <Icon name="chevron" />
      </Menu>
    </>
  )

  // D193: nothing drawn on the open canvas itself (its rim stubs aside).
  const emptyHere =
    !data.nodes.some((n) => (n.canvas_id ?? null) === canvasId && !n.on_edge) &&
    !data.groups.some((g) => (g.canvas_id ?? null) === canvasId) &&
    !subsOf(data).some((sub) => sub.parent_id === canvasId)
  // D193: a canvas switched to (Open, the breadcrumb, Esc) is fitted into view once drawn.
  const shownCanvas = useRef(canvasId)
  useEffect(() => {
    if (shownCanvas.current === canvasId) return
    shownCanvas.current = canvasId
    const timer = setTimeout(() => void flow.fitView({ duration: 200, maxZoom: 1.2, padding: 0.15 }), 80)
    return () => clearTimeout(timer)
  }, [canvasId, flow])

  const dropTarget = (event: DragEvent) => {
    const element = (event.target as HTMLElement).closest('.react-flow__node')
    const id = element?.getAttribute('data-id') ?? null
    return id && data.nodes.some((n) => n.id === id) ? id : null
  }

  return (
    <div
      ref={wrapper}
      className={`canvas${dragging ? ' dragging' : ''}${connecting ? ' connecting' : ''}${readOnly ? ' read-only' : ''}`}
      aria-label="Canvas"
      onDoubleClick={(event: MouseEvent) => {
        if (readOnly) return
        if ((event.target as HTMLElement).classList.contains('react-flow__pane')) onAddNode(position(event))
      }}
      onDragOver={(event) => {
        if (readOnly || !event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (readOnly) return
        const files = Array.from(event.dataTransfer.files)
        if (files.length) onDropFiles(files, dropTarget(event), position(event))
      }}
    >
      <PathChipClick.Provider value={pathChipClick}>
      <ReactFlow
        nodes={nodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(event, node) => {
          // D193: a stub at the rim stands for a record outside the open sub-investigation.
          if (node.type === 'stub') {
            onOpenRecord?.(node.id.slice('stub:'.length))
            return
          }
          // A click with Ctrl, Cmd or Shift adds to the selection instead of replacing it; a
          // group can be part of it too, to add nodes to the group (A20).
          if (event.ctrlKey || event.metaKey || event.shiftKey) {
            onToggleNode(node.id)
            return
          }
          onSelect({ kind: kindOf(node), id: node.id })
        }}
        onNodeDoubleClick={(_, node) => {
          // A128: a double-click opens a box's canvas.
          if (node.type === 'csub') onOpenSub?.(node.id)
        }}
        onEdgeClick={(_, edge) => onSelect({ kind: 'edge', id: edge.id })}
        onPaneClick={() => onSelect(null)}
        onNodeContextMenu={(event, node) => {
          if (node.type === 'stub') return event.preventDefault()
          menuAt(event, { kind: kindOf(node), id: node.id })
        }}
        onEdgeContextMenu={(event, edge) => menuAt(event, { kind: 'edge', id: edge.id })}
        onSelectionContextMenu={(event) => menuAt(event, { kind: 'selection' })}
        onPaneContextMenu={(event) => menuAt(event, { kind: 'pane', at: position(event) })}
        onSelectionEnd={() => {
          // Box selection (Shift-drag); single clicks are handled above, so the app owns the selection.
          const ids = flow
            .getNodes()
            .filter((n) => n.selected)
            .map((n) => n.id)
          if (ids.length) onMultiSelect(ids)
        }}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        edgesReconnectable={!readOnly}
        connectionMode={ConnectionMode.Loose}
        isValidConnection={(connection) => connection.source !== connection.target}
        onConnect={(connection) => {
          if (connection.source && connection.target) onConnect(connection.source, connection.target, sides(connection))
        }}
        onReconnect={(old, connection) => {
          // A29: dragging an arrow's end moves it to another side of the same box. Moving it to
          // another box would make it a different transition, so that is refused.
          if (connection.source !== old.source || connection.target !== old.target) {
            onError("An arrow's end can only move to another side of the same box. To join other boxes, draw a new transition.")
            return
          }
          const ids = (old.data as TransitionData | undefined)?.ids ?? [old.id]
          onMoveEnds(ids, sides(connection))
        }}
        onNodeDragStop={(_, __, dragged) => {
          const moved: Record<string, { x: number; y: number }> = {}
          for (const n of dragged) {
            // D193: a card inside an expanded box moves in the box's own layout.
            const origin = n.parentId ? origins.get(n.parentId) : { x: 0, y: 0 }
            if (!origin) continue // a group's member, placed by its group
            // A node drawn in place of its group moves the group (A23).
            const id = (n.data as { standsFor?: string }).standsFor ?? n.id
            moved[id] = { x: n.position.x + origin.x, y: n.position.y + origin.y }
          }
          if (Object.keys(moved).length) onPositions(moved)
        }}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        multiSelectionKeyCode={['Meta', 'Control', 'Shift']}
        minZoom={0.05}
        fitView
        fitViewOptions={{ maxZoom: 1.2, padding: 0.15 }}
        proOptions={{ hideAttribution: true }}
        colorMode={theme}
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable style={{ width: 150, height: 100 }} nodeColor={(n) => ((n.data as { colour?: string }).colour ?? NO_BRANCH)} />
        {tools === undefined && (
          <Panel position="top-right" className="canvas-tools">
            {canvasTools}
          </Panel>
        )}
        {emptyHere && canvasId !== null && (
          <div className="canvas-empty">
            <EmptyState icon="layers" title="This sub-investigation is empty">
              Move nodes, groups or other sub-investigations into it with Move to… on their menus
              {readOnly ? '.' : ', or double-click here to add a node.'}
            </EmptyState>
          </div>
        )}
        {emptyHere && canvasId === null && (
          <div className="canvas-empty">
            {readOnly ? (
              <EmptyState icon="hexagon" title="This investigation has no nodes">
                Nothing was drawn on its canvas when this copy was made.
              </EmptyState>
            ) : (
              <EmptyState
                icon="hexagon"
                title="An empty canvas"
                actions={
                  <>
                    <button
                      className="primary"
                      onClick={() => {
                        const box = wrapper.current?.getBoundingClientRect()
                        const middle = box ? { clientX: box.left + box.width / 2, clientY: box.top + box.height / 2 } : null
                        onAddNode(middle ? position(middle) : { x: 0, y: 0 })
                      }}
                    >
                      <Icon name="plus" />
                      Add the first node
                    </button>
                    {onImport && (
                      <button onClick={onImport}>
                        <Icon name="upload" />
                        Import output files…
                      </button>
                    )}
                  </>
                }
              >
                Each node is one structure on the mechanism: add one by hand, or import Gaussian, ORCA, xTB or CREST
                outputs with their energies. You can also double-click the canvas to add a node there, or drop output
                files onto it.
              </EmptyState>
            )}
          </div>
        )}
      </ReactFlow>
      </PathChipClick.Provider>
      {tools && createPortal(canvasTools, tools)}
    </div>
  )
}

export const CanvasPane = CanvasView
