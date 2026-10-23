import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { api, STATUS_LABEL, type Canvas, type Node, type Step } from '../api'
import { Icon } from '../icons'
import { Modal } from './Modal'
import { NodeRow } from './NodeRow'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

// D178: which outline sections are collapsed, remembered by the browser.
const COLLAPSED = 'chembook3d.outline.collapsed'

function loadCollapsed(): Set<string> {
  try {
    const stored = JSON.parse(localStorage.getItem(COLLAPSED) ?? '[]') as unknown
    return new Set(Array.isArray(stored) ? stored.filter((s): s is string => typeof s === 'string') : [])
  } catch {
    return new Set()
  }
}

type Fold = { collapsed: boolean; onToggle: () => void }

/** D178: a section's head: a small label that folds the section, its count, and its add buttons
 * (icons, with the old button names as their accessible names). */
function SectionHead({ title, count, fold, children }: { title: string; count: ReactNode; fold: Fold; children?: ReactNode }) {
  return (
    <div className="section-head outline-head">
      <h3>
        <button className="section-toggle" aria-expanded={!fold.collapsed} onClick={fold.onToggle}>
          <Icon name="chevron" />
          {title}
          <span className="count-pill">{count}</span>
        </button>
      </h3>
      {children && <span className="section-actions">{children}</span>}
    </div>
  )
}

/** FR-STEP-01…03: an ordered list of reaction steps with notes. D178: a step reads as text, its
actions show on the row under the pointer or with the keyboard in it; a step moves by its grip,
by ↑ and ↓, or by Alt+↑ and Alt+↓. */
function Steps({ steps, fold, onChanged }: { steps: Step[]; fold: Fold; onChanged: () => void }) {
  const [draft, setDraft] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Step | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState<number | null>(null)
  const dragged = useRef<number | null>(null)
  // Where a dragged step goes: before the step at this index (the length: after the last).
  const [over, setOver] = useState<number | null>(null)
  const rows = useRef(new Map<string, HTMLDivElement>())
  const newStep = useRef<HTMLInputElement>(null)
  // The control that moved a step with the keyboard, focused again once the list has its new order.
  const refocus = useRef<{ id: string; control: string } | null>(null)
  const run = (promise: Promise<unknown>) =>
    promise.then(
      () => {
        setError(null)
        onChanged()
      },
      (err: unknown) => {
        refocus.current = null
        setError(errorText(err))
      },
    )
  const reorder = (from: number, to: number) => {
    if (to < 0 || to >= steps.length || to === from) {
      refocus.current = null
      return
    }
    const ids = steps.map((s) => s.id)
    const [moved] = ids.splice(from, 1)
    ids.splice(to, 0, moved)
    run(api.reorderSteps(ids))
  }
  useEffect(() => {
    const wanted = refocus.current
    const row = wanted && rows.current.get(wanted.id)
    if (!wanted || !row) return
    refocus.current = null
    const control = row.querySelector<HTMLElement>(`[data-control="${wanted.control}"]:not(:disabled)`)
    ;(control ?? row.querySelector('input'))?.focus()
  }, [steps])
  // Steps added in quick succession are created one after another, so they keep their order.
  const adding = useRef<Promise<unknown>>(Promise.resolve())
  const add = () => {
    const name = draft.trim()
    if (!name) return
    adding.current = adding.current.then(() => run(api.createStep(name)))
    setDraft('')
  }
  // Where a step dragged over this row goes: before it in its upper half, after it in its lower.
  const target = (event: DragEvent<HTMLElement>, index: number) => {
    const box = event.currentTarget.getBoundingClientRect()
    return event.clientY < box.top + box.height / 2 ? index : index + 1
  }
  const endDrag = () => {
    dragged.current = null
    setDragging(null)
    setOver(null)
  }

  return (
    <section className="outline-section" aria-label="Reaction steps">
      <SectionHead title="Reaction steps" count={steps.length} fold={fold}>
        <button
          className="small quiet icon"
          aria-label="Add a step"
          title="Add a step: type its name and press Enter"
          onClick={() => {
            if (draft.trim()) add()
            else if (fold.collapsed) fold.onToggle()
            setTimeout(() => newStep.current?.focus())
          }}
        >
          <Icon name="plus" />
        </button>
      </SectionHead>
      {!fold.collapsed && (
        <>
          <ol className="steps">
            {steps.map((step, index) => {
              const name = step.name || 'step'
              const notesOpen = open === step.id
              return (
                <li
                  key={step.id}
                  className={`${dragging === index ? 'dragging' : ''}${over === index ? ' drop-before' : ''}${over === steps.length && index === steps.length - 1 ? ' drop-after' : ''}`}
                  onDragOver={(event) => {
                    if (dragged.current === null) return
                    event.preventDefault()
                    setOver(target(event, index))
                  }}
                  onDrop={(event) => {
                    const from = dragged.current
                    if (from === null) return
                    event.preventDefault()
                    const to = target(event, index)
                    reorder(from, to > from ? to - 1 : to)
                    endDrag()
                  }}
                >
                  <div
                    className={`step-row${notesOpen || step.notes ? ' has-notes' : ''}`}
                    ref={(element) => {
                      if (element) rows.current.set(step.id, element)
                      else rows.current.delete(step.id)
                    }}
                    onKeyDown={(event) => {
                      if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
                      event.preventDefault()
                      const control = (document.activeElement as HTMLElement | null)?.dataset.control ?? 'name'
                      refocus.current = { id: step.id, control }
                      reorder(index, index + (event.key === 'ArrowUp' ? -1 : 1))
                    }}
                  >
                    <span
                      className="grip"
                      draggable
                      aria-hidden="true"
                      title="Drag to move (or Alt+↑ and Alt+↓)"
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = 'move'
                        event.dataTransfer.setData('text/plain', step.name)
                        const row = rows.current.get(step.id)
                        if (row) event.dataTransfer.setDragImage(row, 12, 12)
                        dragged.current = index
                        setDragging(index)
                      }}
                      onDragEnd={endDrag}
                    >
                      <Icon name="grip" />
                    </span>
                    <span className="step-number">{index + 1}</span>
                    <input
                      key={step.name}
                      data-control="name"
                      aria-label={`Name of step ${index + 1}`}
                      title={step.name}
                      defaultValue={step.name}
                      onBlur={(event) => event.target.value !== step.name && run(api.updateStep(step.id, { name: event.target.value }))}
                      onKeyDown={(event) => event.key === 'Enter' && (event.target as HTMLInputElement).blur()}
                    />
                    <span className="count-pill" title="Nodes at this step">
                      {step.node_count}
                    </span>
                    <span className="row-actions">
                      <button
                        className="small quiet icon"
                        data-control="up"
                        aria-label={`Move ${name} up`}
                        title="Move up (Alt+↑)"
                        disabled={index === 0}
                        onClick={() => {
                          refocus.current = { id: step.id, control: 'up' }
                          reorder(index, index - 1)
                        }}
                      >
                        <Icon name="arrowup" />
                      </button>
                      <button
                        className="small quiet icon"
                        data-control="down"
                        aria-label={`Move ${name} down`}
                        title="Move down (Alt+↓)"
                        disabled={index === steps.length - 1}
                        onClick={() => {
                          refocus.current = { id: step.id, control: 'down' }
                          reorder(index, index + 1)
                        }}
                      >
                        <Icon name="arrowdown" />
                      </button>
                      <button
                        className="small quiet icon notes-button"
                        aria-label={`Notes for ${name}`}
                        title={step.notes ? 'Notes (this step has notes)' : 'Notes'}
                        aria-expanded={notesOpen}
                        onClick={() => setOpen(notesOpen ? null : step.id)}
                      >
                        <Icon name="note" />
                      </button>
                      <button className="small quiet icon danger" aria-label={`Delete ${name}`} title="Delete step" onClick={() => setConfirm(step)}>
                        <Icon name="trash" />
                      </button>
                    </span>
                  </div>
                  {notesOpen && (
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
              )
            })}
          </ol>
          <input
            ref={newStep}
            className="add-step"
            aria-label="New step name"
            placeholder="+ Add a step (Enter)"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && add()}
          />
        </>
      )}
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
  fold,
  onSelect,
  onChanged,
}: {
  canvas: Canvas
  selectedId: string | null
  fold: Fold
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
      <SectionHead title="Branches" count={canvas.branches.length} fold={fold}>
        <button
          className="small quiet"
          aria-label="+ Branch"
          title="Add a branch"
          onClick={() =>
            api.createBranch({ name: `Branch ${canvas.branches.length + 1}` }).then(
              (b) => onChanged(b.id),
              (err: unknown) => setError(errorText(err)),
            )
          }
        >
          <Icon name="plus" />
          Branch
        </button>
      </SectionHead>
      <ul className="branches" aria-label="Branch list" hidden={fold.collapsed}>
        {canvas.branches.map((branch) => (
          <li key={branch.id} style={{ paddingLeft: `${Math.min(levelOf(branch.id), 6) * 12}px` }}>
            <button
              className={`branch-item${branch.id === selectedId ? ' selected' : ''}`}
              aria-current={branch.id === selectedId}
              onClick={() => onSelect(branch.id)}
            >
              <span className="swatch" style={{ background: branch.colour }} />
              <span className="branch-name">{branch.name || <em>Unnamed branch</em>}</span>
              <span className={`status status-${branch.status}`}>{STATUS_LABEL[branch.status]}</span>
              <span className="count-pill" title="Nodes on this branch">
                {branch.node_count}
              </span>
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
  fold,
  onSelect,
  onAdd,
  onImport,
}: {
  canvas: Canvas
  selectedId: string | null
  fold: Fold
  onSelect: (id: string) => void
  onAdd: () => void
  onImport: () => void
}) {
  const uses = (id: string) => canvas.transitions.filter((t) => t.species.some((s) => s.species_id === id)).length
  return (
    <section className="outline-section" aria-label="Free species">
      <SectionHead title="Free species" count={canvas.species.length} fold={fold}>
        <button
          className="small quiet icon"
          aria-label="+ Species"
          title="Add a species: a substrate or fragment that joins or leaves on a transition"
          onClick={onAdd}
        >
          <Icon name="plus" />
        </button>
        <button className="small quiet icon" aria-label="Import…" title="Import an output file as a new free species" onClick={onImport}>
          <Icon name="upload" />
        </button>
      </SectionHead>
      <ul aria-label="Free species list" className="node-results" hidden={fold.collapsed}>
        {canvas.species.map((species) => (
          <li key={species.id}>
            <button
              className={`node-item${species.id === selectedId ? ' selected' : ''}`}
              aria-current={species.id === selectedId}
              onClick={() => onSelect(species.id)}
            >
              <span className="node-label">
                <span className="node-name">{species.label || <em>Untitled species</em>}</span>
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
              {species.formula && <span className="node-formula">{species.formula}</span>}
              <span className="count-pill" title="Transitions it joins or leaves on">
                on {uses(species.id)} {uses(species.id) === 1 ? 'edge' : 'edges'}
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
  colours,
  selectedId,
  selectedIds,
  fold,
  onSelect,
  onToggle,
}: {
  nodes: Node[]
  colours: Map<string, string>
  selectedId: string | null
  fold: Fold
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
    <section className={`outline-section${fold.collapsed ? '' : ' grow'}`} aria-label="Node search">
      <SectionHead title="Nodes" count={q ? `${shown.length}/${nodes.length}` : nodes.length} fold={fold} />
      <div className="search-field" hidden={fold.collapsed}>
        <Icon name="search" />
        <input
          type="search"
          aria-label="Search nodes"
          placeholder="Search label, tag, notes"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <ul aria-label="Nodes" className="node-results" hidden={fold.collapsed}>
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
              <NodeRow node={node} colour={node.branch_id ? colours.get(node.branch_id) : undefined}>
                {node.on_edge && (
                  <span className="chip scan-path-chip" title="Shown on its edge only, as a chip (D121)">
                    on edge
                  </span>
                )}
              </NodeRow>
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
  const [collapsed, setCollapsed] = useState(loadCollapsed)
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED, JSON.stringify([...collapsed]))
    } catch {
      // the browser keeps nothing; the sections still fold
    }
  }, [collapsed])
  const fold = (name: string): Fold => ({
    collapsed: collapsed.has(name),
    onToggle: () =>
      setCollapsed((now) => {
        const next = new Set(now)
        if (!next.delete(name)) next.add(name)
        return next
      }),
  })
  const colours = new Map(canvas.branches.map((b) => [b.id, b.colour]))
  return (
    <aside className="outline" aria-label="Outline">
      <div className="buttons outline-actions">
        <button className="primary" onClick={onAdd}>
          <Icon name="plus" />
          Add node
        </button>
        {/* D178: the short label, the full name as accessible name (as the canvas tools, D174) */}
        <button
          aria-label="Import file…"
          onClick={onImport}
          title="Import a Gaussian, ORCA or xTB output as a new node, or a CREST ensemble as a group; or drop files on the canvas"
        >
          <Icon name="upload" />
          Import…
        </button>
      </div>
      <Steps steps={canvas.steps} fold={fold('steps')} onChanged={() => onChanged()} />
      <Branches canvas={canvas} selectedId={selectedBranchId} fold={fold('branches')} onSelect={onSelectBranch} onChanged={onChanged} />
      <FreeSpecies
        canvas={canvas}
        selectedId={selectedNodeId}
        fold={fold('species')}
        onSelect={onSelectNode}
        onAdd={onAddSpecies}
        onImport={onImportSpecies}
      />
      <NodeSearch
        nodes={canvas.nodes}
        colours={colours}
        selectedId={selectedNodeId}
        selectedIds={selectedNodeIds}
        fold={fold('nodes')}
        onSelect={onSelectNode}
        onToggle={onToggleNode}
      />
    </aside>
  )
}
