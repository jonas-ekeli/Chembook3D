import { useRef, useState } from 'react'
import { api, STATUS_LABEL, type Canvas, type Node, type Step } from '../api'
import { Modal } from './Modal'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** FR-STEP-01…03: an ordered list of reaction steps with notes. */
function Steps({ steps, onChanged }: { steps: Step[]; onChanged: () => void }) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Step | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = (promise: Promise<unknown>) =>
    promise.then(
      () => {
        setError(null)
        onChanged()
      },
      (err: unknown) => setError(errorText(err)),
    )
  const move = (index: number, by: number) => {
    const ids = steps.map((s) => s.id)
    const [moved] = ids.splice(index, 1)
    ids.splice(index + by, 0, moved)
    run(api.reorderSteps(ids))
  }
  // Steps added in quick succession are created one after another, so they keep their order.
  const adding = useRef<Promise<unknown>>(Promise.resolve())
  const add = () => {
    const name = draft.trim()
    if (!name) return
    adding.current = adding.current.then(() => run(api.createStep(name)))
    setDraft('')
  }

  return (
    <section className="outline-section" aria-label="Reaction steps">
      <h3>Reaction steps</h3>
      <ol className="steps">
        {steps.map((step, index) => (
          <li key={step.id}>
            <div className="step-row">
              <span className="step-number">{index + 1}</span>
              <input
                key={step.name}
                aria-label={`Name of step ${index + 1}`}
                defaultValue={step.name}
                onBlur={(event) => event.target.value !== step.name && run(api.updateStep(step.id, { name: event.target.value }))}
                onKeyDown={(event) => event.key === 'Enter' && (event.target as HTMLInputElement).blur()}
              />
              <span className="muted small" title="Nodes at this step">
                {step.node_count}
              </span>
              <button className="small icon" aria-label={`Move ${step.name || 'step'} up`} disabled={index === 0} onClick={() => move(index, -1)}>
                ↑
              </button>
              <button
                className="small icon"
                aria-label={`Move ${step.name || 'step'} down`}
                disabled={index === steps.length - 1}
                onClick={() => move(index, 1)}
              >
                ↓
              </button>
              <button
                className="small icon"
                aria-label={`Notes for ${step.name || 'step'}`}
                aria-expanded={open === step.id}
                onClick={() => setOpen(open === step.id ? null : step.id)}
              >
                ✎
              </button>
              <button className="small icon danger" aria-label={`Delete ${step.name || 'step'}`} onClick={() => setConfirm(step)}>
                ×
              </button>
            </div>
            {open === step.id && (
              <textarea
                key={step.notes}
                aria-label={`Notes for step ${step.name}`}
                rows={3}
                defaultValue={step.notes}
                placeholder="Notes on this step"
                onBlur={(event) => event.target.value !== step.notes && run(api.updateStep(step.id, { notes: event.target.value }))}
              />
            )}
          </li>
        ))}
      </ol>
      <div className="add-row">
        <input
          aria-label="New step name"
          placeholder="Add a step"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => event.key === 'Enter' && add()}
        />
        <button onClick={add} disabled={!draft.trim()}>
          Add
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {confirm && (
        <Modal
          title="Delete reaction step?"
          onClose={() => setConfirm(null)}
          actions={
            <>
              <button onClick={() => setConfirm(null)}>Cancel</button>
              <button
                className="danger"
                onClick={() => {
                  run(api.deleteStep(confirm.id))
                  setConfirm(null)
                }}
              >
                Delete step
              </button>
            </>
          }
        >
          <p>
            “{confirm.name || 'Unnamed step'}” is deleted. Its {confirm.node_count} node
            {confirm.node_count === 1 ? ' is' : 's are'} kept, with no step.
          </p>
        </Modal>
      )}
    </section>
  )
}

/** FR-BR-01: branches with their colour; selecting one opens it in the inspector. */
function Branches({
  canvas,
  selectedId,
  onSelect,
  onChanged,
}: {
  canvas: Canvas
  selectedId: string | null
  onSelect: (id: string) => void
  onChanged: (selectId?: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const depth = new Map<string, number>()
  const levelOf = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!
    const branch = canvas.branches.find((b) => b.id === id)
    if (!branch || seen.has(id)) return 0
    seen.add(id)
    const level = branch.parent_ids.length ? 1 + Math.max(...branch.parent_ids.map((p) => levelOf(p, seen))) : 0
    depth.set(id, level)
    return level
  }
  return (
    <section className="outline-section" aria-label="Branches">
      <div className="section-head">
        <h3>Branches</h3>
        <button
          className="small"
          onClick={() =>
            api.createBranch({ name: `Branch ${canvas.branches.length + 1}` }).then(
              (b) => onChanged(b.id),
              (err: unknown) => setError(errorText(err)),
            )
          }
        >
          + Branch
        </button>
      </div>
      <ul className="branches" aria-label="Branch list">
        {canvas.branches.map((branch) => (
          <li key={branch.id} style={{ paddingLeft: `${Math.min(levelOf(branch.id), 6) * 12}px` }}>
            <button
              className={`branch-item${branch.id === selectedId ? ' selected' : ''}`}
              aria-current={branch.id === selectedId}
              onClick={() => onSelect(branch.id)}
            >
              <span className="swatch" style={{ background: branch.colour }} />
              <span className="branch-name">{branch.name || <em>Unnamed branch</em>}</span>
              <span className="muted small">{branch.node_count}</span>
              <span className={`status status-${branch.status}`}>{STATUS_LABEL[branch.status]}</span>
            </button>
          </li>
        ))}
        {canvas.branches.length === 0 && <li className="muted small">No branches yet.</li>}
      </ul>
      {error && <p role="alert">{error}</p>}
    </section>
  )
}

/** D69: free species (substrates, released fragments) kept off the canvas. Choosing one opens it
in the inspector; transitions list the ones that join or leave on them. */
function FreeSpecies({
  canvas,
  selectedId,
  onSelect,
  onAdd,
  onImport,
}: {
  canvas: Canvas
  selectedId: string | null
  onSelect: (id: string) => void
  onAdd: () => void
  onImport: () => void
}) {
  const uses = (id: string) => canvas.transitions.filter((t) => t.species.some((s) => s.species_id === id)).length
  return (
    <section className="outline-section" aria-label="Free species">
      <div className="section-head">
        <h3>Free species</h3>
        <span className="buttons">
          <button className="small" onClick={onAdd} title="A substrate or fragment that joins or leaves on a transition">
            + Species
          </button>
          <button className="small" onClick={onImport} title="Import an output file as a new free species">
            Import…
          </button>
        </span>
      </div>
      <ul aria-label="Free species list" className="node-results">
        {canvas.species.map((species) => (
          <li key={species.id}>
            <button
              className={`node-item${species.id === selectedId ? ' selected' : ''}`}
              aria-current={species.id === selectedId}
              onClick={() => onSelect(species.id)}
            >
              <span className="node-label">
                {species.label || <em>Untitled species</em>}
                {species.warnings.length > 0 && (
                  <span
                    className="warn-icon"
                    role="img"
                    aria-label="Has warnings"
                    title={species.warnings.map((w) => `${w.code}: ${w.message}`).join('\n')}
                  >
                    ⚠
                  </span>
                )}
              </span>
              <span className="node-meta">
                {species.formula && <span>{species.formula}</span>}
                <span className="muted small" title="Transitions it joins or leaves on">
                  on {uses(species.id)} {uses(species.id) === 1 ? 'edge' : 'edges'}
                </span>
              </span>
            </button>
          </li>
        ))}
        {canvas.species.length === 0 && (
          <li className="muted small">None yet. Add substrates or fragments that join or leave a step.</li>
        )}
      </ul>
    </section>
  )
}

/** Search by label, tag or note text (P10); choosing a node selects it and centres the canvas. */
function NodeSearch({
  nodes,
  selectedId,
  selectedIds,
  onSelect,
  onToggle,
}: {
  nodes: Node[]
  selectedId: string | null
  selectedIds: string[]
  onSelect: (id: string) => void
  onToggle: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const shown = q
    ? nodes.filter(
        (n) =>
          n.label.toLowerCase().includes(q) ||
          n.notes.toLowerCase().includes(q) ||
          (n.formula ?? '').toLowerCase().includes(q) ||
          n.tags.some((t) => t.toLowerCase().includes(q)),
      )
    : nodes
  return (
    <section className="outline-section grow" aria-label="Node search">
      <h3>Nodes</h3>
      <input
        type="search"
        aria-label="Search nodes"
        placeholder="Search label, tag, notes"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <ul aria-label="Nodes" className="node-results">
        {shown.map((node) => (
          <li key={node.id}>
            <button
              className={`node-item${node.id === selectedId || selectedIds.includes(node.id) ? ' selected' : ''}`}
              aria-current={node.id === selectedId}
              aria-pressed={selectedIds.includes(node.id)}
              title="Ctrl-click or Shift-click to select several"
              onClick={(event) =>
                event.ctrlKey || event.metaKey || event.shiftKey ? onToggle(node.id) : onSelect(node.id)
              }
            >
              <span className="node-label">
                {node.derived_from_id && <span title="Derived node">↳ </span>}
                {node.label || <em>Untitled node</em>}
                {node.warnings.length > 0 && (
                  <span
                    className="warn-icon"
                    role="img"
                    aria-label="Has warnings"
                    title={node.warnings.map((w) => `${w.code}: ${w.message}`).join('\n')}
                  >
                    ⚠
                  </span>
                )}
              </span>
              <span className="node-meta">
                <span className={`status status-${node.status}`}>{STATUS_LABEL[node.status]}</span>
                {node.on_edge && (
                  <span className="chip scan-path-chip" title="Shown on its edge only, as a chip (D121)">
                    on edge
                  </span>
                )}
                {node.formula && <span>{node.formula}</span>}
              </span>
            </button>
          </li>
        ))}
        {nodes.length === 0 && <li className="muted empty">No nodes yet.</li>}
        {nodes.length > 0 && shown.length === 0 && <li className="muted empty">No matches.</li>}
      </ul>
    </section>
  )
}

/** The outline beside the canvas (P6): reaction steps, branches and node search. */
export function Outline({
  canvas,
  selectedNodeId,
  selectedNodeIds,
  selectedBranchId,
  onSelectNode,
  onToggleNode,
  onSelectBranch,
  onAdd,
  onImport,
  onAddSpecies,
  onImportSpecies,
  onChanged,
}: {
  canvas: Canvas
  selectedNodeId: string | null
  selectedNodeIds: string[]
  selectedBranchId: string | null
  onSelectNode: (id: string) => void
  onToggleNode: (id: string) => void
  onSelectBranch: (id: string) => void
  onAdd: () => void
  onImport: () => void
  onAddSpecies: () => void
  onImportSpecies: () => void
  onChanged: (selectBranchId?: string) => void
}) {
  return (
    <aside className="outline" aria-label="Outline">
      <div className="buttons outline-actions">
        <button className="primary" onClick={onAdd}>
          + Add node
        </button>
        <button onClick={onImport} title="Import a Gaussian, ORCA or xTB output as a new node, or a CREST ensemble as a group; or drop files on the canvas">
          Import file…
        </button>
      </div>
      <Steps steps={canvas.steps} onChanged={() => onChanged()} />
      <Branches canvas={canvas} selectedId={selectedBranchId} onSelect={onSelectBranch} onChanged={onChanged} />
      <FreeSpecies
        canvas={canvas}
        selectedId={selectedNodeId}
        onSelect={onSelectNode}
        onAdd={onAddSpecies}
        onImport={onImportSpecies}
      />
      <NodeSearch
        nodes={canvas.nodes}
        selectedId={selectedNodeId}
        selectedIds={selectedNodeIds}
        onSelect={onSelectNode}
        onToggle={onToggleNode}
      />
    </aside>
  )
}
