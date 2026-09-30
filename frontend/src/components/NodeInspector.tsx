import { useEffect, useState } from 'react'
import {
  api,
  geometryToXyz,
  speciesChip,
  ROLES,
  STATUSES,
  type Canvas,
  type GeometryRows,
  type HistoryEntry,
  type Node,
  type NodeDeletePreview,
  type NodeFields,
} from '../api'
import { CalculationList } from './CalculationList'
import { Notes, TextField } from './Fields'
import { HistoryList } from './HistoryList'
import { Modal } from './Modal'
import { SplitDialog } from './PathwayInspectors'
import { recordNames } from '../names'
import { Vibrations } from './Vibrations'
import { Viewer3D } from './Viewer3D'
import { XyzEditor } from './XyzEditor'

const SYSTEM_TAGS = new Set(['optimization-incomplete'])

function IntField({
  label,
  value,
  min,
  onCommit,
}: {
  label: string
  value: number | null
  min?: number
  onCommit: (value: number | null) => void
}) {
  const shown = value === null ? '' : String(value)
  const [draft, setDraft] = useState(shown)
  const valid = draft.trim() === '' || /^[-+]?\d+$/.test(draft.trim())
  const commit = () => {
    if (!valid || draft === shown) return
    onCommit(draft.trim() === '' ? null : Number.parseInt(draft, 10))
  }
  return (
    <label className="field">
      <span>{label}</span>
      <input
        inputMode="numeric"
        value={draft}
        min={min}
        aria-invalid={!valid}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
      />
    </label>
  )
}

function Tags({ tags, onChange }: { tags: string[]; onChange: (tags: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const tag = draft.trim()
    if (tag && !tags.includes(tag)) onChange([...tags, tag])
    setDraft('')
  }
  return (
    <div className="field">
      <span>Tags</span>
      <div className="tags">
        {tags.map((tag) => (
          <span key={tag} className={`tag${SYSTEM_TAGS.has(tag) ? ' system' : ''}`}>
            {tag}
            <button
              className="tag-remove"
              aria-label={`Remove tag ${tag}`}
              onClick={() => onChange(tags.filter((t) => t !== tag))}
            >
              ×
            </button>
          </span>
        ))}
        <input
          aria-label="New tag"
          placeholder="Add tag"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') add()
          }}
          onBlur={add}
        />
      </div>
    </div>
  )
}

export function NodeInspector({
  node,
  canvas,
  refreshKey,
  onChanged,
  onSelect,
  onSelectGroup,
  onSelectBranch,
  onSelectTransition,
  onDeleted,
  onImport,
  onDropFiles,
  onRefresh,
  isReference,
  onUseAsReference,
}: {
  node: Node
  canvas: Canvas
  refreshKey: number
  onChanged: (node: Node, notice?: string) => void
  onSelect: (id: string) => void
  onSelectGroup: (id: string) => void
  onSelectBranch: (id: string) => void
  onSelectTransition: (id: string) => void
  onDeleted: () => void
  onImport: () => void
  onDropFiles: (files: File[]) => void
  onRefresh: () => void
  isReference: boolean
  onUseAsReference: () => void
}) {
  const nodes = [...canvas.nodes, ...canvas.species]
  // D69: a free species has no step, branch, group or edges; transitions list it instead.
  const isSpecies = node.kind === 'species'
  const [dragging, setDragging] = useState(false)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<NodeDeletePreview | null>(null)
  const [splitting, setSplitting] = useState(false)
  const [vibration, setVibration] = useState<{ xyz: string } | null>(null)

  useEffect(() => {
    api.history(node.id).then(setHistory, (err: unknown) => setError(String(err)))
  }, [node.id, refreshKey])

  const update = (fields: NodeFields) => {
    api.updateNode(node.id, fields).then(
      (updated) => {
        setError(null)
        onChanged(updated)
      },
      (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
    )
  }

  const setKind = (kind: Node['kind']) =>
    api.setNodeKind(node.id, kind).then(
      (updated) => {
        setError(null)
        onChanged(updated, kind === 'species' ? 'Now a free species, listed under Free species.' : 'Now a node on the canvas.')
      },
      (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
    )

  const restore = (entry: HistoryEntry) => {
    if (entry.field === 'kind') {
      void setKind(entry.old_value as Node['kind'])
    } else if (entry.field === 'geometry') {
      api.setGeometry(node.id, geometryToXyz(entry.old_value as GeometryRows, node.label)).then(
        (result) =>
          onChanged(
            result.node,
            result.derived ? 'The old coordinates were saved as a new derived node.' : undefined,
          ),
        (err: unknown) => setError(err instanceof Error ? err.message : String(err)),
      )
    } else if (entry.field) {
      update({ [entry.field]: entry.old_value } as NodeFields)
    }
  }

  const parent = node.derived_from_id ? nodes.find((n) => n.id === node.derived_from_id) : null
  const children = nodes.filter((n) => n.derived_from_id === node.id)
  const name = (n: Node) => n.label || (n.kind === 'species' ? 'Untitled species' : 'Untitled node')
  const branch = canvas.branches.find((b) => b.id === node.branch_id)
  const group = canvas.groups.find((g) => g.id === node.group_id)
  const originBranch = canvas.branches.find((b) => b.id === node.origin_branch_id)
  const endName = (id: string) => {
    const other = nodes.find((n) => n.id === id)
    if (other) return name(other)
    const g = canvas.groups.find((x) => x.id === id)
    return g ? `${g.label || 'Group'} (group)` : 'deleted'
  }
  const incoming = canvas.transitions.filter((t) => t.target_id === node.id)
  const outgoing = canvas.transitions.filter((t) => t.source_id === node.id)
  const carriedBy = canvas.transitions.filter((t) => t.species.some((s) => s.species_id === node.id))

  return (
    <div
      className={`inspector${dragging ? ' dragging' : ''}`}
      aria-label="Node inspector"
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes('Files')) return
        event.preventDefault()
        event.stopPropagation()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        event.stopPropagation()
        setDragging(false)
        const files = Array.from(event.dataTransfer.files)
        if (files.length) onDropFiles(files)
      }}
    >
      <div className="inspector-head">
        <h2>{name(node)}</h2>
        {isSpecies && <span className="badge species">Free species</span>}
        <span className="spacer" />
        {!isSpecies && (
          <button
            onClick={onUseAsReference}
            disabled={isReference}
            title="Energy mode and profiles show values relative to this node (EN-8)"
          >
            {isReference ? 'Energy reference' : 'Use as energy reference'}
          </button>
        )}
        <button onClick={onImport} title="Import an output file onto this record; or drop it here">
          {isSpecies ? 'Import onto species…' : 'Import onto node…'}
        </button>
        <button
          className="danger"
          onClick={() =>
            api.deletePreview(node.id).then(
              (preview) => setConfirmDelete(preview),
              (err: unknown) => setError(String(err)),
            )
          }
        >
          {isSpecies ? 'Delete species' : 'Delete node'}
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      {node.warnings.length > 0 && (
        <ul className="warnings" aria-label="Warnings">
          {node.warnings.map((w, i) => (
            <li key={i}>
              <span className="badge warn">{w.code}</span> {w.message}
            </li>
          ))}
        </ul>
      )}

      <div className="facts">
        <span>
          Formula: <strong>{node.formula ?? '—'}</strong>
        </span>
        <span>{node.atom_count} atoms</span>
        <span>
          {node.calculation_count} calculation{node.calculation_count === 1 ? '' : 's'}
        </span>
        {node.derived_from_id && (
          <span>
            Derived from{' '}
            {parent ? (
              <button className="link" onClick={() => onSelect(parent.id)}>
                {name(parent)}
              </button>
            ) : (
              <em>a deleted node</em>
            )}
          </span>
        )}
        {children.length > 0 && (
          <span>
            Derived nodes:{' '}
            {children.map((child, i) => (
              <span key={child.id}>
                {i > 0 && ', '}
                <button className="link" onClick={() => onSelect(child.id)}>
                  {name(child)}
                </button>
              </span>
            ))}
          </span>
        )}
        {group && (
          <span>
            Member of{' '}
            <button className="link" onClick={() => onSelectGroup(group.id)}>
              {group.label || 'a group'}
            </button>
            {group.representative_id === node.id && <strong> (representative)</strong>}
            {originBranch && originBranch.id !== node.branch_id && <> · came from {originBranch.name || 'a branch'}</>}
          </span>
        )}
      </div>

      <div className="inspector-grid">
        <div className="column">
          <section className="fields" aria-label="Details">
            <TextField key={node.label} label="Label" value={node.label} onCommit={(label) => update({ label })} />
            <label className="field">
              <span>Status</span>
              <select
                value={node.status}
                onChange={(event) => update({ status: event.target.value as Node['status'] })}
              >
                {STATUSES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </label>
            {!isSpecies && (
              <label className="field">
                <span>Role</span>
                <select
                  value={node.role}
                  onChange={(event) => update({ role: event.target.value as Node['role'] })}
                >
                  {ROLES.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!isSpecies && (
              <label className="field">
                <span>Step</span>
                <select value={node.step_id ?? ''} onChange={(event) => update({ step_id: event.target.value || null })}>
                  <option value="">No step</option>
                  {canvas.steps.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name || 'Unnamed step'}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {!isSpecies && (
              <div className="field">
                <span>Branch</span>
                <span className="field-with-action">
                  <select
                    aria-label="Branch"
                    value={node.branch_id ?? ''}
                    onChange={(event) => update({ branch_id: event.target.value || null })}
                  >
                    <option value="">No branch</option>
                    {canvas.branches.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name || 'Unnamed branch'}
                      </option>
                    ))}
                  </select>
                  {branch && (
                    <button className="small" onClick={() => onSelectBranch(branch.id)} aria-label="Open branch">
                      ↗
                    </button>
                  )}
                </span>
              </div>
            )}
            <div className="field-pair">
              <IntField
                key={`c${node.charge}`}
                label="Charge"
                value={node.charge} onCommit={(charge) => update({ charge })} />
              <IntField
                key={`m${node.multiplicity}`}
                label="Multiplicity"
                value={node.multiplicity}
                min={1}
                onCommit={(multiplicity) => update({ multiplicity })}
              />
            </div>
            <Tags tags={node.tags} onChange={(tags) => update({ tags })} />
          </section>
          <Notes key={node.notes} notes={node.notes} onSave={(notes) => update({ notes })} />
        </div>
        <div className="column">
          <Viewer3D
            models={node.xyz ? [{ xyz: node.xyz }] : []}
            vibration={vibration}
            rotation={node.view_rotation}
            onSaveRotation={isSpecies ? undefined : (view_rotation) => update({ view_rotation })}
          />
          <Vibrations nodeId={node.id} refreshKey={refreshKey} onChange={setVibration} />
          <XyzEditor
            key={node.xyz ?? ''}
            node={node}
            onSaved={(saved, derived) =>
              onChanged(saved, derived ? `Saved as a new node derived from “${name(node)}”.` : undefined)
            }
          />
        </div>
      </div>

      {isSpecies ? (
        <section aria-label="Transitions">
          <div className="section-head">
            <h3>Joins or leaves on</h3>
            <button
              disabled={carriedBy.length > 0}
              title={
                carriedBy.length
                  ? 'Remove it from its transitions first'
                  : 'Draw it on the canvas as an ordinary node (D69)'
              }
              onClick={() => setKind('node')}
            >
              Make it a node
            </button>
          </div>
          {carriedBy.length === 0 ? (
            <p className="muted">No transition yet. Select a transition to add this species as joining or leaving.</p>
          ) : (
            <ul className="plain transitions">
              {carriedBy.map((t) => {
                const entry = t.species.find((s) => s.species_id === node.id)!
                return (
                  <li key={t.id}>
                    <button className="link" onClick={() => onSelectTransition(t.id)}>
                      {endName(t.source_id)} → {endName(t.target_id)}
                    </button>
                    <span className={`chip species-${entry.direction}`}>{speciesChip(entry)}</span>
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      ) : (
        <section aria-label="Transitions">
          <div className="section-head">
            <h3>Transitions</h3>
            {incoming.length + outgoing.length === 0 && !group && (
              <button
                title="A substrate or fragment that joins or leaves on a transition; kept off the canvas (D69)"
                onClick={() => setKind('species')}
              >
                Make it a free species
              </button>
            )}
            <button
              disabled={!branch}
              title={branch ? 'Create child branches of this node’s branch (FR-BR-02)' : 'Assign a branch first'}
              onClick={() => setSplitting(true)}
            >
              Split into branches…
            </button>
          </div>
          {incoming.length + outgoing.length === 0 ? (
            <p className="muted">None. Drag from a node’s right handle to another node on the canvas.</p>
          ) : (
            <ul className="plain transitions">
              {[...incoming, ...outgoing].map((t) => (
                <li key={t.id}>
                  <button className="link" onClick={() => onSelectTransition(t.id)}>
                    {t.target_id === node.id ? `from ${endName(t.source_id)}` : `to ${endName(t.target_id)}`}
                  </button>
                  {t.direct && <span className="no-ts">no TS</span>}
                  <span className={`status status-${t.status}`}>{STATUSES.find((x) => x.value === t.status)?.label}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section aria-label="Calculations section">
        <h3>Calculations</h3>
        <CalculationList nodeId={node.id} refreshKey={refreshKey} onChanged={onRefresh} />
      </section>

      <section aria-label="Node history">
        <h3>History of this node</h3>
        <HistoryList entries={history} names={recordNames(canvas)} onRestore={restore} />
      </section>

      {confirmDelete && (
        <Modal
          title={isSpecies ? 'Delete species?' : 'Delete node?'}
          onClose={() => setConfirmDelete(null)}
          actions={
            <>
              <button onClick={() => setConfirmDelete(null)}>Cancel</button>
              <button
                className="danger"
                onClick={() =>
                  api.deleteNode(node.id).then(
                    () => {
                      setConfirmDelete(null)
                      onDeleted()
                    },
                    (err: unknown) => setError(String(err)),
                  )
                }
              >
                Delete
              </button>
            </>
          }
        >
          <p>This deletes:</p>
          <ul>
            <li>
              the {isSpecies ? 'free species' : 'node'} “{name(node)}”
            </li>
            <li>
              {confirmDelete.calculations} calculation{confirmDelete.calculations === 1 ? '' : 's'}{' '}
              on it
            </li>
            <li>
              {confirmDelete.transitions.length} transition{confirmDelete.transitions.length === 1 ? '' : 's'}
              {confirmDelete.transitions.length > 0 && ':'}
              {confirmDelete.transitions.length > 0 && (
                <ul aria-label="Transitions deleted with the node">
                  {confirmDelete.transitions.map((t) => (
                    <li key={t.id}>
                      {endName(t.source_id)} → {endName(t.target_id)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          </ul>
          {confirmDelete.species_on.length > 0 && (
            <>
              <p>It is taken off these transitions, which stay:</p>
              <ul aria-label="Transitions the species is taken off">
                {confirmDelete.species_on.map((t) => (
                  <li key={t.id}>
                    {endName(t.source_id)} → {endName(t.target_id)}
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="muted">No other transition is changed (INV-7).</p>
          <p className="muted">The deletion is recorded in the investigation history.</p>
        </Modal>
      )}
      {splitting && branch && (
        <SplitDialog
          node={node}
          branch={branch}
          onClose={() => setSplitting(false)}
          onDone={(created) => {
            setSplitting(false)
            onChanged(node, `Created ${created.map((b) => b.name || 'a branch').join(' and ')}.`)
          }}
        />
      )}
    </div>
  )
}
