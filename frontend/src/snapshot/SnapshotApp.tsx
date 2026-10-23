import { ReactFlowProvider } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { energyTypeName, type Canvas, type EnergyType, type GroupLayout } from '../api'
import { EnergyNotes } from '../components/EnergyNotes'
import type { HydrogenMode } from '../chem'
import type { Filters, Selection, ViewMode } from '../canvasView'
import { CanvasPane, type CanvasEnergy } from '../components/Canvas'
import { FilterMenu } from '../components/FilterMenu'
import { GModelBadge } from '../components/GModelBadge'
import { NodeRow } from '../components/NodeRow'
import { HydrogenDisplay } from '../display'
import { Icon } from '../icons'
import { NoteImageSource } from '../notes'
import { energyView, openingView, referenceIds, rememberView, sharedSettings, type SnapshotData } from './data'
import { SnapshotDrawer } from './Drawer'
import { AboutPanel, BranchPanel, GroupPanel, NodePanel, TransitionPanel } from './Panels'

type PanelSelection = Selection | { kind: 'branch'; id: string }

/** Steps with their notes, branches, free species and a node search, to find things by. */
function SharedOutline({
  canvas,
  selectedId,
  onSelectNode,
  onSelectBranch,
}: {
  canvas: Canvas
  selectedId: string | null
  onSelectNode: (id: string) => void
  onSelectBranch: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const nodes = q
    ? canvas.nodes.filter(
        (n) =>
          n.label.toLowerCase().includes(q) ||
          n.notes.toLowerCase().includes(q) ||
          (n.formula ?? '').toLowerCase().includes(q) ||
          n.tags.some((t) => t.toLowerCase().includes(q)),
      )
    : canvas.nodes
  const colours = new Map(canvas.branches.map((b) => [b.id, b.colour]))
  return (
    <aside className="outline" aria-label="Outline">
      <section className="outline-section" aria-label="Reaction steps">
        <h3>Reaction steps</h3>
        <ol className="steps shared-steps">
          {canvas.steps.map((step) => (
            <li key={step.id} title={step.notes || undefined}>
              {step.name || 'Unnamed step'} <span className="muted small">{step.node_count}</span>
            </li>
          ))}
          {canvas.steps.length === 0 && <li className="muted small">None.</li>}
        </ol>
      </section>
      <section className="outline-section" aria-label="Branches">
        <h3>Branches</h3>
        <ul className="plain">
          {canvas.branches.map((b) => (
            <li key={b.id}>
              <button className="link" onClick={() => onSelectBranch(b.id)}>
                <span className="swatch" style={{ background: b.colour }} /> {b.name || 'Unnamed branch'}
              </button>{' '}
              <span className="muted small">{b.node_count}</span>
            </li>
          ))}
          {canvas.branches.length === 0 && <li className="muted small">None.</li>}
        </ul>
      </section>
      {canvas.species.length > 0 && (
        <section className="outline-section" aria-label="Free species">
          <h3>Free species</h3>
          <ul className="plain">
            {canvas.species.map((s) => (
              <li key={s.id}>
                <button className="link" onClick={() => onSelectNode(s.id)}>
                  {s.label || 'Untitled species'}
                </button>{' '}
                {s.formula && <span className="muted small">{s.formula}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section className="outline-section grow" aria-label="Node search">
        <h3>Nodes</h3>
        <div className="search-field">
          <Icon name="search" />
          <input
            type="search"
            aria-label="Search nodes"
            placeholder="Search label, tag, notes"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <ul aria-label="Nodes" className="node-results">
          {nodes.map((node) => (
            <li key={node.id}>
              <button
                className={`node-item${node.id === selectedId ? ' selected' : ''}`}
                aria-current={node.id === selectedId}
                onClick={() => onSelectNode(node.id)}
              >
                <NodeRow node={node} colour={colours.get(node.branch_id ?? '')} />
              </button>
            </li>
          ))}
          {canvas.nodes.length > 0 && nodes.length === 0 && <li className="muted empty">No matches.</li>}
        </ul>
      </section>
    </aside>
  )
}

/** The read-only copy of an investigation (D79): the canvas, the side panel and the energy
 * drawer of the app, with everything that edits left out. The reader's choices (view mode,
 * filters, level, type, reference, expanded groups) change only what is shown. It opens on the
 * view the app showed when it was made, and remembers the reader's own choices (D128). */
export function SnapshotApp({ data }: { data: SnapshotData }) {
  const settings = useMemo(() => sharedSettings(data), [data])
  const references = useMemo(() => referenceIds(data), [data])
  const [canvas, setCanvas] = useState(data.canvas)
  const [selection, setSelection] = useState<PanelSelection>(null)
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null)
  const [opening] = useState(() => openingView(data))
  const [mode, setMode] = useState<ViewMode>(opening.mode)
  const [filters, setFilters] = useState<Filters>(opening.filters)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(opening.expanded))
  const [hydrogens, setHydrogens] = useState<HydrogenMode>(opening.hydrogens)
  const noteImage = useCallback((id: string) => data.note_images?.[id], [data])
  const [energyLevel, setEnergyLevel] = useState<string | null>(opening.level)
  const [energyType, setEnergyType] = useState<EnergyType>(opening.energyType)
  const [edgeEnergies, setEdgeEnergies] = useState(opening.edgeEnergies)
  const [referenceId, setReferenceId] = useState<string | null>(opening.referenceId)
  const [drawerOpen, setDrawerOpen] = useState(opening.drawerOpen)
  const [drawerTab, setDrawerTab] = useState(opening.drawerTab)
  const [inspectorWidth, setInspectorWidth] = useState(520)
  const resizing = useRef<{ x: number; width: number } | null>(null)

  // As in the app: the chosen level, or the first with G (D32), or the first.
  const options = data.energy_options
  const levelOption =
    options.levels.find((l) => l.key === energyLevel) ??
    options.levels.find((l) => l.types.includes('G')) ??
    options.levels[0] ??
    null
  const shownType: EnergyType = levelOption
    ? levelOption.types.includes(energyType)
      ? energyType
      : levelOption.types.includes('G')
        ? 'G'
        : levelOption.types[0]
    : energyType
  const levelKey = levelOption?.key ?? null
  const view = useMemo(
    () => (levelKey ? energyView(data, levelKey, shownType, referenceId) : null),
    [data, levelKey, shownType, referenceId],
  )
  const canvasEnergy: CanvasEnergy = useMemo(
    () => ({ view, type: shownType, showEdges: edgeEnergies, referenceId, settings }),
    [view, shownType, edgeEnergies, referenceId, settings],
  )

  useEffect(() => {
    rememberView(data, {
      mode,
      level: energyLevel,
      energyType,
      edgeEnergies,
      referenceId,
      filters,
      expanded: [...expanded],
      drawerOpen,
      drawerTab,
      hydrogens,
    })
  }, [data, mode, energyLevel, energyType, edgeEnergies, referenceId, filters, expanded, drawerOpen, drawerTab, hydrogens])

  const toggleGroup = useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  // A21: the layout button only changes how this copy draws the group.
  const setGroupLayout = useCallback((id: string, layout: GroupLayout) => {
    setCanvas((current) => ({ ...current, groups: current.groups.map((g) => (g.id === id ? { ...g, layout } : g)) }))
  }, [])
  const ignore = useCallback(() => undefined, [])

  const selectNode = (id: string) => {
    setSelection({ kind: 'node', id })
    const onCanvas = canvas.nodes.find((n) => n.id === id)
    if (onCanvas) setFocus({ id: onCanvas.group_id ?? id, n: Date.now() })
  }
  const selectGroup = (id: string) => {
    setSelection({ kind: 'group', id })
    setFocus({ id, n: Date.now() })
  }
  const selectTransition = (id: string) => {
    setSelection({ kind: 'edge', id })
    const edge = canvas.transitions.find((t) => t.id === id)
    const source = edge && canvas.nodes.find((n) => n.id === edge.source_id)
    if (edge) setFocus({ id: source?.group_id ?? edge.source_id, n: Date.now() })
  }
  const select = {
    onSelectNode: selectNode,
    onSelectGroup: selectGroup,
    onSelectBranch: (id: string) => setSelection({ kind: 'branch', id }),
    onSelectTransition: selectTransition,
  }
  // D121: a scan path chip opens its path node, as the copy opens any node.
  const openPaths = (id: string) => {
    const first = canvas.transitions.find((t) => t.id === id)?.scan_paths?.[0]
    if (first) selectNode(first.node_id)
    else selectTransition(id)
  }
  const selectPoint = (id: string) => (canvas.groups.some((g) => g.id === id) ? selectGroup(id) : selectNode(id))

  const selectedNode =
    selection?.kind === 'node'
      ? (canvas.nodes.find((n) => n.id === selection.id) ?? canvas.species.find((n) => n.id === selection.id))
      : undefined
  const selectedGroup = selection?.kind === 'group' ? canvas.groups.find((g) => g.id === selection.id) : undefined
  const selectedEdge = selection?.kind === 'edge' ? canvas.transitions.find((t) => t.id === selection.id) : undefined
  const selectedBranch = selection?.kind === 'branch' ? canvas.branches.find((b) => b.id === selection.id) : undefined

  let panel
  if (selectedNode) {
    panel = (
      <NodePanel
        key={selectedNode.id}
        node={selectedNode}
        data={data}
        select={select}
        isReference={referenceId === selectedNode.id}
        canBeReference={references.has(selectedNode.id)}
        onUseAsReference={() => setReferenceId(selectedNode.id)}
      />
    )
  } else if (selectedGroup) {
    panel = (
      <GroupPanel
        key={selectedGroup.id}
        group={selectedGroup}
        canvas={canvas}
        select={select}
        energy={{ view, typeName: shownType, settings }}
      />
    )
  } else if (selectedEdge) {
    panel = (
      <TransitionPanel
        key={selectedEdge.id}
        transition={selectedEdge}
        canvas={canvas}
        select={select}
        settings={settings}
      />
    )
  } else if (selectedBranch) {
    panel = <BranchPanel key={selectedBranch.id} branch={selectedBranch} canvas={canvas} select={select} />
  } else {
    panel = <AboutPanel data={data} select={select} />
  }

  return (
    <HydrogenDisplay value={hydrogens}>
      {/* D85: pictures in pinned notes travel inside the file. */}
      <NoteImageSource.Provider value={noteImage}>
      <div className="app">
        <header className="topbar">
          <strong className="brand">Chembook3D</strong>
          <span className="investigation">{data.investigation.name}</span>
          <span className="badge read-only" title="Exported from Chembook3D; nothing here can be changed">
            Read-only copy
          </span>
          <nav className="tabs" aria-label="Views">
            <button aria-pressed={selection === null} onClick={() => setSelection(null)}>
              Overview
            </button>
          </nav>
          <div className="segmented" role="group" aria-label="Node view">
            <button aria-pressed={mode === 'compact'} onClick={() => setMode('compact')}>
              Compact
            </button>
            <button aria-pressed={mode === 'energy'} onClick={() => setMode('energy')}>
              Energy
            </button>
            <button aria-pressed={mode === 'structure'} onClick={() => setMode('structure')}>
              Structure
            </button>
          </div>
          <FilterMenu canvas={canvas} filters={filters} onChange={setFilters} />
          <div className="energy-select" role="group" aria-label="Energy view">
            <select
              aria-label="Level of theory"
              value={levelKey ?? ''}
              disabled={!levelOption}
              onChange={(event) => setEnergyLevel(event.target.value)}
            >
              {!levelOption && <option value="">No energies</option>}
              {options.levels.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
            <select
              aria-label="Energy type"
              value={shownType}
              disabled={!levelOption}
              onChange={(event) => setEnergyType(event.target.value as EnergyType)}
            >
              {(levelOption?.types ?? []).map((t) => (
                <option key={t} value={t}>
                  {energyTypeName(
                    t,
                    options.temperature,
                    options.cutoff,
                    options.standard_state,
                    levelOption?.job_temperatures,
                  )}
                </option>
              ))}
            </select>
            {shownType === 'G' && <GModelBadge models={levelOption?.g_models} />}
            {options.standard_state === '1 M' && (shownType === 'G' || shownType === 'G_qh') && (
              <span
                className="badge"
                title="Free energies are at the 1 M standard state: each node and free species includes RT ln(V_m / 1 L mol⁻¹)"
              >
                1 M
              </span>
            )}
            <EnergyNotes notes={view?.notes} badges />
            <label className="check" title="Show ΔX on transitions (FR-CAN-03)">
              <input type="checkbox" checked={edgeEnergies} onChange={(event) => setEdgeEnergies(event.target.checked)} />
              <span>Energies on edges</span>
            </label>
          </div>
          <button aria-pressed={drawerOpen} onClick={() => setDrawerOpen(!drawerOpen)}>
            Profile and table
          </button>
          <span className="spacer" />
          <label className="inline">
            <span>Hydrogens</span>
            <select
              aria-label="Hydrogens"
              value={hydrogens}
              onChange={(event) => setHydrogens(event.target.value as HydrogenMode)}
            >
              <option value="all">Show all</option>
              <option value="polar">Hide those bonded to carbon</option>
              <option value="none">Hide all</option>
            </select>
          </label>
        </header>
        <div className="work-column">
          <div className="workspace">
            <SharedOutline
              canvas={canvas}
              selectedId={selection?.kind === 'node' ? selection.id : null}
              onSelectNode={selectNode}
              onSelectBranch={select.onSelectBranch}
            />
            <ReactFlowProvider>
              <CanvasPane
                data={canvas}
                mode={mode}
                energy={canvasEnergy}
                filters={filters}
                selection={selection && selection.kind !== 'branch' ? selection : null}
                multi={[]}
                focus={focus}
                expanded={expanded}
                onToggleGroup={toggleGroup}
                onGroupLayout={setGroupLayout}
                onSelect={setSelection}
                onMultiSelect={ignore}
                onToggleNode={ignore}
                onConnect={ignore}
                onMoveEnds={ignore}
                onAddNode={ignore}
                onDropFiles={ignore}
                onPositions={ignore}
                onError={ignore}
                onOpenPaths={openPaths}
                readOnly
              />
            </ReactFlowProvider>
            <div
              className="splitter"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize inspector"
              onPointerDown={(event) => {
                resizing.current = { x: event.clientX, width: inspectorWidth }
                event.currentTarget.setPointerCapture(event.pointerId)
              }}
              onPointerMove={(event) => {
                if (!resizing.current) return
                const width = resizing.current.width - (event.clientX - resizing.current.x)
                setInspectorWidth(Math.min(1100, Math.max(340, width)))
              }}
              onPointerUp={() => (resizing.current = null)}
            />
            <main className="side" style={{ width: inspectorWidth }}>
              {panel}
            </main>
          </div>
          <div hidden={!drawerOpen}>
            <SnapshotDrawer
              data={data}
              settings={settings}
              level={levelKey}
              levelLabel={levelOption?.label ?? ''}
              type={shownType}
              referenceId={referenceId}
              onReference={setReferenceId}
              tab={drawerTab}
              onTab={setDrawerTab}
              onSelectNode={selectPoint}
              selectedId={selection && selection.kind !== 'edge' && selection.kind !== 'branch' ? selection.id : null}
            />
          </div>
        </div>
      </div>
      </NoteImageSource.Provider>
    </HydrogenDisplay>
  )
}
