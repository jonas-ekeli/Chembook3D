import {
  applyNodeChanges,
  Background,
  BaseEdge,
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
  useReactFlow,
  useUpdateNodeInternals,
  type Edge,
  type EdgeProps,
  type Node as FlowNode,
  type NodeChange,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { toPng, toSvg } from 'html-to-image'
import { memo, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent } from 'react'
import {
  balanceText,
  FADED_STATUSES,
  formatDelta,
  speciesChip,
  STATUS_LABEL,
  type Canvas as CanvasData,
  type EnergyView,
  type Group,
  type GroupLayout,
  type Node,
  type Settings,
  type Transition,
} from '../api'
import { MoleculeSketch } from './MoleculeSketch'

import type { Filters, Selection, ViewMode } from '../canvasView'

const NO_BRANCH = '#98a2b3'

function speciesChips(t: Transition): TransitionData['species'] {
  return t.species.map((s) => ({
    text: speciesChip(s),
    direction: s.direction,
    title: `${s.label}${s.formula ? ` (${s.formula})` : ''} ${s.direction} here`,
  }))
}
const NODE_WIDTH = 176
const MEMBER_CELL = { compact: { w: 196, h: 86 }, energy: { w: 196, h: 86 }, structure: { w: 196, h: 196 } }

/** What the canvas shows of the energy view (FR-CAN-03, FR-CAN-04 energy mode). */
export type CanvasEnergy = {
  view: EnergyView | null
  type: string
  showEdges: boolean
  referenceId: string | null
  settings: Settings | null
}

/** "ΔG 12.34", or "n/a" with its reason as a tooltip. */
type EnergyText = { text: string; title: string }
const GROUP_HEADER = 48

type StructureData = {
  node: Node
  colour: string
  stepName: string | null
  mode: ViewMode
  faded: boolean
  representative: boolean
  energy: EnergyText | null
  /** The group this node is drawn in place of, when the filters leave it alone (A23). */
  standsFor?: string
}
type GroupData = {
  group: Group
  colour: string
  stepName: string | null
  memberCount: number
  representativeLabel: string | null
  /** The representative's geometry, drawn on the collapsed group in structure mode (D68). */
  representativeXyz: string | null
  mode: ViewMode
  ts: boolean
  expanded: boolean
  onToggle: (id: string) => void
  onLayout: (id: string, layout: GroupLayout) => void
  energy: EnergyText | null
}
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
  energy: EnergyText | null
  /** D69: free species joining ("+ propene") or leaving ("− C₂H₄") on the edge. */
  species: { text: string; direction: string; title: string }[]
  /** W-BALANCE messages, when the atoms or charge do not balance. */
  warnings: string[]
}

const StructureNode = memo(function StructureNode({ data, selected }: NodeProps<FlowNode<StructureData>>) {
  const { node, colour, stepName, mode, faded, representative, energy } = data
  const warnings = node.warnings.map((w) => `${w.code}: ${w.message}`).join('\n')
  return (
    <div
      className={`cnode${selected ? ' selected' : ''}${faded ? ' faded' : ''}${representative ? ' representative' : ''}`}
      style={{ ['--branch' as string]: colour }}
      data-testid="canvas-node"
    >
      <Handle type="target" position={Position.Left} />
      <div className="cnode-head">
        {node.role === 'transition_state' && (
          <span className="ts-mark" title="Transition state">
            ‡
          </span>
        )}
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
      </div>
      {mode === 'structure' && <MoleculeSketch xyz={node.xyz} width={NODE_WIDTH - 16} height={120} />}
      {energy && (
        <div className={`cnode-energy${mode === 'structure' ? ' below-structure' : ''}`} title={energy.title}>
          {energy.text}
        </div>
      )}
      <div className="cnode-meta">
        <span className={`status status-${node.status}`}>{STATUS_LABEL[node.status]}</span>
        {stepName && <span className="cnode-step">{stepName}</span>}
      </div>
      <Handle type="source" position={Position.Right} />
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
  const { group, colour, stepName, memberCount, representativeLabel, representativeXyz, mode, ts, expanded } = data
  const { onToggle, onLayout, energy } = data
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
      <Handle type="target" position={Position.Left} />
      <div className="cgroup-head">
        {ts && (
          <span className="ts-mark" title="Transition states">
            ‡
          </span>
        )}
        <span className="cnode-label">{group.label || <em>Group</em>}</span>
        <span className="muted small">
          {memberCount} member{memberCount === 1 ? '' : 's'}
        </span>
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
        <MoleculeSketch xyz={representativeXyz} width={NODE_WIDTH - 16} height={120} />
      )}
      {!expanded && energy && (
        <div className={`cnode-energy${mode === 'structure' ? ' below-structure' : ''}`} title={energy.title}>
          {energy.text}
        </div>
      )}
      {!expanded && (
        <div className="cnode-meta">
          <span className="small">
            {representativeLabel ? <>★ {representativeLabel}</> : <em className="muted">no representative</em>}
          </span>
          {stepName && <span className="cnode-step">{stepName}</span>}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  )
})

function TransitionEdge(props: EdgeProps<Edge<TransitionData>>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, selected } =
    props
  let [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition })
  const dx = targetX - sourceX
  if (Math.abs(dx) > 200 && Math.abs(targetY - sourceY) < 60) {
    // An edge that skips a column (e.g. a direct connection past a TS) arcs over the nodes
    // in between instead of running behind them.
    const lift = Math.min(160, Math.abs(dx) * 0.25)
    const c1 = [sourceX + dx * 0.3, sourceY - lift]
    const c2 = [targetX - dx * 0.3, targetY - lift]
    path = `M ${sourceX},${sourceY} C ${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${targetX},${targetY}`
    labelX = (sourceX + 3 * c1[0] + 3 * c2[0] + targetX) / 8
    labelY = (sourceY + 3 * c1[1] + 3 * c2[1] + targetY) / 8
  }
  if (!data) return null
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
      <EdgeLabelRenderer>
        <div
          className={`edge-label${data.faded ? ' faded' : ''}`}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
        >
          {data.energy && (
            <span className="edge-energy" title={data.energy.title}>
              {data.energy.text}
            </span>
          )}
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
          {data.warnings.length > 0 && (
            <span className="warn-icon" role="img" aria-label="Does not balance" title={data.warnings.join('\n')}>
              ⚠
            </span>
          )}
          {data.direct && <span className="no-ts">no TS</span>}
          <span className={`status-dot status-${data.status}`} title={STATUS_LABEL[data.status]} />
        </div>
      </EdgeLabelRenderer>
    </>
  )
}

const nodeTypes = { structure: StructureNode, cgroup: GroupBox }
const edgeTypes = { transition: TransitionEdge }

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
  onSelect,
  onMultiSelect,
  onToggleNode,
  onConnect,
  onAddNode,
  onDropFiles,
  onPositions,
  onError,
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
  onSelect: (selection: Selection) => void
  onMultiSelect: (nodeIds: string[]) => void
  onToggleNode: (id: string) => void
  onConnect: (source: string, target: string) => void
  onAddNode: (position: { x: number; y: number }) => void
  onDropFiles: (files: File[], targetId: string | null, position: { x: number; y: number }) => void
  onPositions: (positions: Record<string, { x: number; y: number }>) => void
  onError: (message: string) => void
}) {
  const flow = useReactFlow()
  const updateNodeInternals = useUpdateNodeInternals()
  const wrapper = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)

  const colours = useMemo(() => new Map(data.branches.map((b) => [b.id, b.colour])), [data.branches])
  const stepNames = useMemo(() => new Map(data.steps.map((s) => [s.id, s.name || 'Unnamed step'])), [data.steps])

  // Nodes show energies only in energy mode, and in structure mode once a reference is chosen
  // (D73, ΔX below the structure), so only then do energy changes rebuild them. Rebuilding
  // every node when the energy view arrives, just after the canvas loaded, left React Flow
  // re-measuring nodes and occasionally dropping edges or a click.
  const nodeEnergySource =
    mode === 'energy' || (mode === 'structure' && energy.referenceId) ? energy : null

  const { flowNodes, flowEdges: plainEdges } = useMemo(() => {
    // D43: in energy mode a node shows ΔX from the reference node, never an absolute value.
    // D72: the server balances it with every free species that joined or left between the
    // reference and the node, so it matches the profile along that route.
    const nodeEnergy = (id: string): EnergyText | null => {
      const energy = nodeEnergySource
      if (!energy?.view) return null
      if (!energy.referenceId) return { text: `Δ${energy.type}: no reference`, title: 'Choose a reference node' }
      if (energy.view.reference_id !== energy.referenceId) return { text: `Δ${energy.type} …`, title: 'Updating' }
      const own = energy.view.relative[id]
      if (own?.value == null) return { text: `Δ${energy.type} n/a`, title: own?.message ?? 'no value' }
      const unit = energy.settings?.energy_unit ?? 'kcal/mol'
      const title = own.species.length
        ? `${unit}, balanced with ${balanceText(own.species)}`
        : own.joined
          ? unit
          : `${unit}; not joined to the reference by transitions, so no free species are counted`
      return { text: `Δ${energy.type} ${formatDelta(own.value, energy.settings)}`, title }
    }
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
    const cell = MEMBER_CELL[mode]
    const chosen = new Set(multi)
    const isSelected = (id: string) => chosen.has(id) || (selection?.kind === 'node' && selection.id === id)

    const result: FlowNode[] = []
    // Where each record is drawn: itself, or its collapsed group.
    const drawnAs = new Map<string, string>()
    const branchColour = (id: string | null) => (id ? (colours.get(id) ?? NO_BRANCH) : NO_BRANCH)

    for (const group of data.groups) {
      const members = group.member_ids.map((id) => byId.get(id)).filter((n): n is Node => !!n)
      // D66: members keep their branch, so the filters apply to each member. A group with
      // none left is hidden; one the filters leave with a single member is drawn as that
      // node, in the group's place (A23).
      const shown = members.filter(nodeShown)
      if (hidden.steps.has(group.step_id ?? 'none')) continue
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
          position: { x: group.pos_x, y: group.pos_y },
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
        position: { x: group.pos_x, y: group.pos_y },
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
          mode,
          // A25: a group of transition states is marked like one.
          ts: members.length > 0 && members.every((m) => m.role === 'transition_state'),
          expanded: open,
          onToggle: onToggleGroup,
          onLayout: onGroupLayout,
          energy: nodeEnergy(group.id),
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
          } satisfies StructureData,
        })
      })
    }

    for (const node of data.nodes) {
      if (node.group_id || !nodeShown(node)) continue
      drawnAs.set(node.id, node.id)
      result.push({
        id: node.id,
        type: 'structure',
        position: { x: node.pos_x, y: node.pos_y },
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
        } satisfies StructureData,
      })
    }

    const groupsById = new Map(data.groups.map((g) => [g.id, g]))
    const edges: Edge<TransitionData>[] = []
    // A24: edges joining the same two drawn boxes (e.g. one per conformer between two
    // collapsed groups) are drawn as one line.
    const between = new Map<string, Edge<TransitionData>>()
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
        merged.colour = merged.colour === colour ? colour : NO_BRANCH
        merged.direct &&= t.direct
        merged.crossBranch &&= t.cross_branch
        merged.faded &&= FADED_STATUSES.has(t.status)
        for (const s of speciesChips(t)) if (!merged.species.some((x) => x.text === s.text)) merged.species.push(s)
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
          species: speciesChips(t),
          warnings: t.warnings.map((w) => `${w.code}: ${w.message}`),
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
    return { flowNodes: result, flowEdges: edges }
  }, [data, mode, nodeEnergySource, filters, expanded, selection, multi, colours, stepNames, onToggleGroup, onGroupLayout])

  // FR-EN-02: ΔX = X(target) − X(source) on each edge, or n/a (EN-3).
  const flowEdges = useMemo(() => {
    const view = energy.view
    if (!energy.showEdges || !view) return plainEdges
    const edgeEnergy = ({ ids, representative }: TransitionData): EnergyText | null => {
      // A24, D70: a line standing for several edges shows the representatives' edge, if any.
      const id = ids.length === 1 ? ids[0] : representative
      const found = id ? view.edges[id] : undefined
      if (!found) return null
      const note = ids.length > 1 ? ' (the transition between the representatives)' : ''
      return found.delta === null
        ? { text: 'n/a', title: (found.message ?? 'one end has no value at this level and type') + note }
        : {
            text: `Δ${energy.type} ${formatDelta(found.delta, energy.settings)}`,
            title: (energy.settings?.energy_unit ?? 'kcal/mol') + note,
          }
    }
    return plainEdges.map((e) => ({ ...e, data: { ...e.data!, energy: edgeEnergy(e.data!) } }))
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

  const exportImage = async (what: 'viewport' | 'full', format: 'png' | 'svg') => {
    setExportOpen(false)
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
      backgroundColor: '#ffffff',
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

  const dropTarget = (event: DragEvent) => {
    const element = (event.target as HTMLElement).closest('.react-flow__node')
    const id = element?.getAttribute('data-id') ?? null
    return id && data.nodes.some((n) => n.id === id) ? id : null
  }

  return (
    <div
      ref={wrapper}
      className={`canvas${dragging ? ' dragging' : ''}`}
      aria-label="Canvas"
      onDoubleClick={(event: MouseEvent) => {
        if ((event.target as HTMLElement).classList.contains('react-flow__pane')) onAddNode(position(event))
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        const files = Array.from(event.dataTransfer.files)
        if (files.length) onDropFiles(files, dropTarget(event), position(event))
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(event, node) => {
          // A click with Ctrl, Cmd or Shift adds to the selection instead of replacing it; a
          // group can be part of it too, to add nodes to the group (A20).
          if (event.ctrlKey || event.metaKey || event.shiftKey) {
            onToggleNode(node.id)
            return
          }
          onSelect({ kind: node.type === 'cgroup' ? 'group' : 'node', id: node.id })
        }}
        onEdgeClick={(_, edge) => onSelect({ kind: 'edge', id: edge.id })}
        onPaneClick={() => onSelect(null)}
        onSelectionEnd={() => {
          // Box selection (Shift-drag); single clicks are handled above, so the app owns the selection.
          const ids = flow
            .getNodes()
            .filter((n) => n.selected)
            .map((n) => n.id)
          if (ids.length) onMultiSelect(ids)
        }}
        onConnect={(connection) => {
          if (connection.source && connection.target) onConnect(connection.source, connection.target)
        }}
        onNodeDragStop={(_, __, dragged) => {
          const moved: Record<string, { x: number; y: number }> = {}
          for (const n of dragged) {
            if (n.parentId) continue
            // A node drawn in place of its group moves the group (A23).
            const id = (n.data as { standsFor?: string }).standsFor ?? n.id
            moved[id] = { x: n.position.x, y: n.position.y }
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
      >
        <Background gap={24} size={1} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable style={{ width: 150, height: 100 }} nodeColor={(n) => ((n.data as { colour?: string }).colour ?? NO_BRANCH)} />
        <Panel position="top-right" className="canvas-tools">
          <button onClick={() => flow.fitView({ nodes: flow.getNodes().filter((n) => n.selected), duration: 300, maxZoom: 1.5 })}>
            Fit to selection
          </button>
          <div className="menu">
            <button aria-haspopup="menu" aria-expanded={exportOpen} onClick={() => setExportOpen(!exportOpen)}>
              Export image ▾
            </button>
            {exportOpen && (
              <div role="menu" className="menu-list">
                <button role="menuitem" onClick={() => exportImage('viewport', 'png')}>
                  Visible area as PNG
                </button>
                <button role="menuitem" onClick={() => exportImage('full', 'png')}>
                  Whole canvas as PNG
                </button>
                <button role="menuitem" onClick={() => exportImage('viewport', 'svg')}>
                  Visible area as SVG
                </button>
                <button role="menuitem" onClick={() => exportImage('full', 'svg')}>
                  Whole canvas as SVG
                </button>
              </div>
            )}
          </div>
        </Panel>
        {data.nodes.length === 0 && data.groups.length === 0 && (
          <Panel position="top-center" className="canvas-empty muted">
            Double-click to add a node, or drop output files here.
          </Panel>
        )}
      </ReactFlow>
    </div>
  )
}

export const CanvasPane = CanvasView
