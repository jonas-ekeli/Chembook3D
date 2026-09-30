import { useEffect, useState } from 'react'
import {
  api,
  formatDelta,
  SIDES,
  speciesChip,
  STATUSES,
  type Branch,
  type Canvas,
  type EnergyView,
  type Group,
  type GroupDeletePreview,
  type Node,
  type NodeDeletePreview,
  type Overlay,
  type Settings,
  type Side,
  type SpeciesDirection,
  type Status,
  type Transition,
} from '../api'
import { Modal } from './Modal'
import { Notes, TextField } from './Fields'
import { Viewer3D } from './Viewer3D'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const nodeName = (n: Node | undefined) => (n ? n.label || 'Untitled node' : 'a deleted node')
const branchName = (b: Branch | undefined) => (b ? b.name || 'Unnamed branch' : '—')

function StatusField({ value, onChange }: { value: Status; onChange: (status: Status) => void }) {
  return (
    <label className="field">
      <span>Status</span>
      <select value={value} onChange={(event) => onChange(event.target.value as Status)}>
        {STATUSES.map((s) => (
          <option key={s.value} value={s.value}>
            {s.label}
          </option>
        ))}
      </select>
    </label>
  )
}

/** D76: which side of a box an arrow's end is drawn on. */
function SideField({ label, value, onChange }: { label: string; value: Side; onChange: (side: Side) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value as Side)}>
        {SIDES.map((side) => (
          <option key={side} value={side}>
            {side[0].toUpperCase() + side.slice(1)}
          </option>
        ))}
      </select>
    </label>
  )
}

/** One endpoint of a transition, as a link that selects it. */
function EndLink({
  canvas,
  id,
  onSelectNode,
  onSelectGroup,
}: {
  canvas: Canvas
  id: string
  onSelectNode: (id: string) => void
  onSelectGroup: (id: string) => void
}) {
  const node = canvas.nodes.find((n) => n.id === id)
  if (node) {
    return (
      <button className="link" onClick={() => onSelectNode(id)}>
        {nodeName(node)}
      </button>
    )
  }
  const group = canvas.groups.find((g) => g.id === id)
  return group ? (
    <button className="link" onClick={() => onSelectGroup(id)}>
      {group.label || 'Group'} (group)
    </button>
  ) : (
    <em>deleted</em>
  )
}

/** FR-SPC-03: free species that join or leave on a transition, and W-BALANCE (D69). */
function TransitionSpeciesSection({
  transition,
  canvas,
  onChanged,
  onSelectNode,
}: {
  transition: Transition
  canvas: Canvas
  onChanged: () => void
  onSelectNode: (id: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const available = canvas.species.filter((s) => !transition.species.some((e) => e.species_id === s.id))
  const [speciesId, setSpeciesId] = useState('')
  const [direction, setDirection] = useState<SpeciesDirection>('joins')
  const [count, setCount] = useState(1)
  const chosen = available.some((s) => s.id === speciesId) ? speciesId : (available[0]?.id ?? '')
  const run = (promise: Promise<unknown>) =>
    promise.then(
      () => {
        setError(null)
        onChanged()
      },
      (err: unknown) => setError(errorText(err)),
    )
  return (
    <section aria-label="Free species on this transition">
      <h3>Free species</h3>
      {transition.warnings.length > 0 && (
        <ul className="warnings" aria-label="Transition warnings">
          {transition.warnings.map((w, i) => (
            <li key={i}>
              <span className="badge warn">{w.code}</span> {w.message}
            </li>
          ))}
        </ul>
      )}
      {transition.species.length === 0 ? (
        <p className="muted small">
          None. Add a substrate that joins here, or a fragment that leaves, so energies stay balanced.
        </p>
      ) : (
        <ul className="plain species-list" aria-label="Species on this transition">
          {transition.species.map((entry) => (
            <li key={entry.species_id}>
              <button
                className={`chip species-${entry.direction}`}
                title={entry.formula ?? undefined}
                onClick={() => onSelectNode(entry.species_id)}
              >
                {speciesChip(entry)}
              </button>
              <select
                aria-label={`Direction of ${entry.label}`}
                value={entry.direction}
                onChange={(event) =>
                  run(
                    api.attachSpecies(transition.id, entry.species_id, event.target.value as SpeciesDirection, entry.count),
                  )
                }
              >
                <option value="joins">joins</option>
                <option value="leaves">leaves</option>
              </select>
              <input
                key={entry.count}
                type="number"
                min={1}
                aria-label={`Count of ${entry.label}`}
                className="count"
                defaultValue={entry.count}
                onBlur={(event) => {
                  const value = Number(event.target.value)
                  if (Number.isInteger(value) && value >= 1 && value !== entry.count)
                    run(api.attachSpecies(transition.id, entry.species_id, entry.direction, value))
                }}
              />
              <button
                className="small icon danger"
                aria-label={`Remove ${entry.label}`}
                onClick={() => run(api.detachSpecies(transition.id, entry.species_id))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {canvas.species.length === 0 ? (
        <p className="muted small">Create one under Free species in the outline first.</p>
      ) : (
        available.length > 0 && (
          <div className="species-add" role="group" aria-label="Add a free species">
            <select aria-label="Species to add" value={chosen} onChange={(event) => setSpeciesId(event.target.value)}>
              {available.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label || 'Untitled species'}
                  {s.formula ? ` (${s.formula})` : ''}
                </option>
              ))}
            </select>
            <select
              aria-label="Joins or leaves"
              value={direction}
              onChange={(event) => setDirection(event.target.value as SpeciesDirection)}
            >
              <option value="joins">joins</option>
              <option value="leaves">leaves</option>
            </select>
            <input
              type="number"
              min={1}
              className="count"
              aria-label="Count"
              value={count}
              onChange={(event) => setCount(Math.max(1, Math.round(Number(event.target.value) || 1)))}
            />
            <button disabled={!chosen} onClick={() => run(api.attachSpecies(transition.id, chosen, direction, count))}>
              Add
            </button>
          </div>
        )
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}

/** FR-EDGE-01…03: a transition's ends, status and notes; direct connections say "no TS". */
export function TransitionInspector({
  transition,
  canvas,
  onChanged,
  onDeleted,
  onSelectNode,
  onSelectGroup,
}: {
  transition: Transition
  canvas: Canvas
  onChanged: () => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
  onSelectGroup: (id: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const update = (fields: Partial<Pick<Transition, 'status' | 'notes' | 'source_side' | 'target_side'>>) =>
    api.updateTransition(transition.id, fields).then(onChanged, (err: unknown) => setError(errorText(err)))
  const ends = { canvas, onSelectNode, onSelectGroup }
  return (
    <div className="inspector" aria-label="Transition inspector">
      <div className="inspector-head">
        <h2>Transition</h2>
        <span className="spacer" />
        <button className="danger" onClick={() => setConfirm(true)}>
          Delete transition
        </button>
      </div>
      <p className="transition-ends">
        <EndLink id={transition.source_id} {...ends} /> → <EndLink id={transition.target_id} {...ends} />
      </p>
      <div className="facts">
        {transition.direct ? (
          <span className="no-ts" title="Neither end is a transition-state node (D53)">
            Direct connection: no TS
          </span>
        ) : (
          <span>Through a transition-state node</span>
        )}
        {transition.cross_branch && <span>Between branches (interconversion)</span>}
      </div>
      <section className="fields">
        <StatusField value={transition.status} onChange={(status) => update({ status })} />
        <SideField
          label="Arrow leaves from"
          value={transition.source_side}
          onChange={(source_side) => update({ source_side })}
        />
        <SideField
          label="Arrow arrives at"
          value={transition.target_side}
          onChange={(target_side) => update({ target_side })}
        />
      </section>
      <TransitionSpeciesSection
        transition={transition}
        canvas={canvas}
        onChanged={onChanged}
        onSelectNode={onSelectNode}
      />
      <Notes key={transition.notes} notes={transition.notes} onSave={(notes) => update({ notes })} />
      {error && <p role="alert">{error}</p>}
      {confirm && (
        <Modal
          title="Delete transition?"
          onClose={() => setConfirm(false)}
          actions={
            <>
              <button onClick={() => setConfirm(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() => api.deleteTransition(transition.id).then(onDeleted, (err: unknown) => setError(errorText(err)))}
              >
                Delete
              </button>
            </>
          }
        >
          <p>Only this transition is removed; both nodes stay.</p>
        </Modal>
      )}
    </div>
  )
}

/** FR-BR-01, FR-BR-04: a branch's fields, parents, full lineage, children and nodes. */
export function BranchInspector({
  branch,
  canvas,
  onChanged,
  onDeleted,
  onSelectNode,
  onSelectBranch,
  onArranged,
}: {
  branch: Branch
  canvas: Canvas
  onChanged: () => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
  onSelectBranch: (id: string) => void
  onArranged: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  const update = (fields: Parameters<typeof api.updateBranch>[1]) =>
    api.updateBranch(branch.id, fields).then(onChanged, (err: unknown) => setError(errorText(err)))
  const members = canvas.nodes.filter((n) => n.branch_id === branch.id)
  const splitNode = canvas.nodes.find((n) => n.id === branch.split_node_id)
  const startedBy = canvas.groups.find((g) => g.outgoing_branch_id === branch.id)
  const link = (id: string) => (
    <button key={id} className="link" onClick={() => onSelectBranch(id)}>
      {branchName(byId.get(id))}
    </button>
  )

  return (
    <div className="inspector" aria-label="Branch inspector">
      <div className="inspector-head">
        <span className="swatch large" style={{ background: branch.colour }} />
        <h2>{branchName(branch)}</h2>
        <span className="spacer" />
        <button onClick={() => api.arrangeBranch(branch.id).then(onArranged, (err: unknown) => setError(errorText(err)))}>
          Arrange branch
        </button>
        <button className="danger" onClick={() => setConfirm(true)}>
          Delete branch
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <section aria-label="Lineage" className="lineage">
        <h3>Lineage</h3>
        {branch.lineage_paths.map((path) => (
          <p key={path.join('-')} className="lineage-path">
            {path.map((id, i) => (
              <span key={id}>
                {i > 0 && ' → '}
                {id === branch.id ? <strong>{branchName(branch)}</strong> : link(id)}
              </span>
            ))}
          </p>
        ))}
        <p className="muted small">
          {branch.child_ids.length ? <>Children: {branch.child_ids.map((id, i) => <span key={id}>{i > 0 && ', '}{link(id)}</span>)}</> : 'No child branches.'}
        </p>
        {splitNode && (
          <p className="muted small">
            Split from{' '}
            <button className="link" onClick={() => onSelectNode(splitNode.id)}>
              {nodeName(splitNode)}
            </button>
          </p>
        )}
        {startedBy && <p className="muted small">Starts at group “{startedBy.label || 'Group'}”.</p>}
      </section>
      <section className="fields" aria-label="Details">
        <TextField key={branch.name} label="Name" value={branch.name} onCommit={(name) => update({ name })} />
        <label className="field">
          <span>Colour</span>
          <input
            type="color"
            aria-label="Colour"
            value={branch.colour}
            onChange={(event) => update({ colour: event.target.value })}
          />
        </label>
        <StatusField value={branch.status} onChange={(status) => update({ status })} />
        <fieldset className="field parents">
          <legend>Parent branches</legend>
          {canvas.branches
            .filter((b) => b.id !== branch.id)
            .map((b) => (
              <label key={b.id} className="check">
                <input
                  type="checkbox"
                  checked={branch.parent_ids.includes(b.id)}
                  onChange={(event) =>
                    update({
                      parent_ids: event.target.checked
                        ? [...branch.parent_ids, b.id]
                        : branch.parent_ids.filter((p) => p !== b.id),
                    })
                  }
                />
                <span className="swatch" style={{ background: b.colour }} /> {branchName(b)}
              </label>
            ))}
        </fieldset>
      </section>
      <Notes key={branch.notes} notes={branch.notes} onSave={(notes) => update({ notes })} />
      <section aria-label="Branch nodes">
        <h3>Nodes ({members.length})</h3>
        <ul className="plain">
          {members.map((n) => (
            <li key={n.id}>
              <button className="link" onClick={() => onSelectNode(n.id)}>
                {nodeName(n)}
              </button>
              {n.step_id && <span className="muted small"> · {canvas.steps.find((s) => s.id === n.step_id)?.name}</span>}
            </li>
          ))}
        </ul>
      </section>
      {confirm && (
        <Modal
          title="Delete branch?"
          onClose={() => setConfirm(false)}
          actions={
            <>
              <button onClick={() => setConfirm(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() =>
                  api.deleteBranch(branch.id).then(onDeleted, (err: unknown) => {
                    setConfirm(false)
                    setError(errorText(err))
                  })
                }
              >
                Delete branch
              </button>
            </>
          }
        >
          <p>
            “{branchName(branch)}” is deleted. Its {members.length} node{members.length === 1 ? ' is' : 's are'} kept, with
            no branch.
          </p>
        </Modal>
      )}
    </div>
  )
}

/** Choosing how to remove a group (D54): dissolve, or delete with everything in it. */
function RemoveGroupDialog({
  group,
  onClose,
  onDone,
}: {
  group: Group
  onClose: () => void
  onDone: () => void
}) {
  const [preview, setPreview] = useState<GroupDeletePreview | null>(null)
  const [choice, setChoice] = useState<'dissolve' | 'delete'>('dissolve')
  const [restore, setRestore] = useState(true)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.groupDeletePreview(group.id).then(setPreview, (err: unknown) => setError(errorText(err)))
  }, [group.id])
  const go = () =>
    (choice === 'dissolve' ? api.dissolveGroup(group.id, restore) : api.deleteGroup(group.id)).then(onDone, (err: unknown) =>
      setError(errorText(err)),
    )
  return (
    <Modal
      title="Remove group?"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="danger" disabled={!preview} onClick={go}>
            {choice === 'dissolve' ? 'Dissolve group' : 'Delete group with contents'}
          </button>
        </>
      }
    >
      {preview && (
        <div className="remove-group">
          <label className="check">
            <input type="radio" name="remove" checked={choice === 'dissolve'} onChange={() => setChoice('dissolve')} />
            <span>
              <strong>Dissolve</strong>: the {preview.members.length} members stay as nodes.
              {preview.group_transitions > 0 && ` The group's ${preview.group_transitions} transition(s) are removed.`}
            </span>
          </label>
          {choice === 'dissolve' && (
            <label className="check indent">
              <input type="checkbox" checked={restore} onChange={(event) => setRestore(event.target.checked)} />
              <span>Keep members in their branches (otherwise they have no branch)</span>
            </label>
          )}
          <label className="check">
            <input type="radio" name="remove" checked={choice === 'delete'} onChange={() => setChoice('delete')} />
            <span>
              <strong>Delete with contents</strong>: this deletes
            </span>
          </label>
          {choice === 'delete' && (
            <ul aria-label="Deleted with the group">
              {preview.members.map((m) => (
                <li key={m.id}>
                  the node “{m.label || 'Untitled node'}” with {m.calculation_count} calculation
                  {m.calculation_count === 1 ? '' : 's'}
                </li>
              ))}
              <li>
                {preview.all_transitions} transition{preview.all_transitions === 1 ? '' : 's'} touching the group or its
                members
              </li>
            </ul>
          )}
          <p className="muted small">Either way the change is recorded in the investigation history.</p>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** FR-IMP-11: removing a member deletes that node, after a confirmation that lists what goes
 * with it (P3). */
function RemoveMemberDialog({
  member,
  canvas,
  onClose,
  onDone,
}: {
  member: Node
  canvas: Canvas
  onClose: () => void
  onDone: () => void
}) {
  const [preview, setPreview] = useState<NodeDeletePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.deletePreview(member.id).then(setPreview, (err: unknown) => setError(errorText(err)))
  }, [member.id])
  const endName = (id: string) => {
    const group = canvas.groups.find((g) => g.id === id)
    return group ? group.label || 'Group' : nodeName(canvas.nodes.find((n) => n.id === id))
  }
  return (
    <Modal
      title="Remove member?"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="danger"
            disabled={!preview}
            onClick={() => api.deleteNode(member.id).then(onDone, (err: unknown) => setError(errorText(err)))}
          >
            Remove and delete
          </button>
        </>
      }
    >
      {error && <p role="alert">{error}</p>}
      {preview && (
        <>
          <p>
            “{nodeName(member)}” is removed from the group and deleted, with its {preview.calculations} calculation
            {preview.calculations === 1 ? '' : 's'}
            {preview.transitions.length > 0 ? ' and these transitions:' : '.'}
          </p>
          {preview.transitions.length > 0 && (
            <ul aria-label="Transitions deleted with the member">
              {preview.transitions.map((t) => (
                <li key={t.id}>
                  {endName(t.source_id)} → {endName(t.target_id)}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Modal>
  )
}

/** FR-GRP-01/02/05: members, the user's representative, branches in and out. */
export function GroupInspector({
  group,
  canvas,
  onChanged,
  onRemoved,
  onSelectNode,
  onSelectBranch,
  energy,
}: {
  group: Group
  canvas: Canvas
  onChanged: () => void
  onRemoved: () => void
  onSelectNode: (id: string) => void
  onSelectBranch: (id: string) => void
  energy: { view: EnergyView | null; typeName: string; settings: Settings | null }
}) {
  const [error, setError] = useState<string | null>(null)
  const [removing, setRemoving] = useState(false)
  const [removingMember, setRemovingMember] = useState<Node | null>(null)
  const [sorted, setSorted] = useState(false)
  const update = (fields: Parameters<typeof api.updateGroup>[1]) =>
    api.updateGroup(group.id, fields).then(onChanged, (err: unknown) => setError(errorText(err)))
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  const listed = group.member_ids.map((id) => canvas.nodes.find((n) => n.id === id)).filter((n): n is Node => !!n)
  // FR-GRP-03: members can be sorted by energy at the view's level and type. Sorting only
  // orders the list; the representative stays the user's choice (EN-10).
  const valueOf = (id: string) => energy.view?.values[id]?.value ?? null
  const known = listed.map((m) => valueOf(m.id)).filter((v): v is number => v !== null)
  const lowest = known.length ? Math.min(...known) : null
  const members =
    sorted && energy.view
      ? [...listed].sort((a, b) => (valueOf(a.id) ?? Infinity) - (valueOf(b.id) ?? Infinity))
      : listed

  return (
    <div className="inspector" aria-label="Group inspector">
      <div className="inspector-head">
        <h2>{group.label || 'Group'}</h2>
        <span className="spacer" />
        <button className="danger" onClick={() => setRemoving(true)}>
          Remove group…
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <section className="fields" aria-label="Details">
        <TextField key={group.label} label="Label" value={group.label} onCommit={(label) => update({ label })} />
        <label className="field">
          <span>Step</span>
          <select value={group.step_id ?? ''} onChange={(event) => update({ step_id: event.target.value || null })}>
            <option value="">No step</option>
            {canvas.steps.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name || 'Unnamed step'}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Outgoing branch</span>
          <select
            value={group.outgoing_branch_id ?? ''}
            onChange={(event) => update({ outgoing_branch_id: event.target.value || null })}
          >
            <option value="">None</option>
            {canvas.branches.map((b) => (
              <option key={b.id} value={b.id}>
                {branchName(b)}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>Incoming branches</span>
          <span>
            {group.incoming_branch_ids.length
              ? group.incoming_branch_ids.map((id, i) => (
                  <span key={id}>
                    {i > 0 && ', '}
                    <button className="link" onClick={() => onSelectBranch(id)}>
                      {branchName(byId.get(id))}
                    </button>
                  </span>
                ))
              : '—'}
          </span>
        </div>
      </section>
      <section aria-label="Members">
        <h3>Members ({members.length})</h3>
        <p className="muted small">
          The representative is the member the outgoing path continues from. Only you choose it (D18).
        </p>
        {energy.view && (
          <label className="check">
            <input type="checkbox" checked={sorted} onChange={(event) => setSorted(event.target.checked)} />
            <span>Sort by {energy.typeName}</span>
          </label>
        )}
        <table className="members">
          <thead>
            <tr>
              <th>Representative</th>
              <th>Node</th>
              <th>Branch</th>
              {energy.view && <th>Δ{energy.typeName} from lowest</th>}
              <th />
            </tr>
          </thead>
          {/* Uncontrolled, so a click shows at once; the key resets them to the stored choice. */}
          <tbody key={group.representative_id ?? 'none'}>
            <tr>
              <td>
                <input
                  type="radio"
                  name={`rep-${group.id}`}
                  aria-label="No representative"
                  defaultChecked={group.representative_id === null}
                  onChange={() => update({ representative_id: null })}
                />
              </td>
              <td colSpan={energy.view ? 4 : 3} className="muted">
                none
              </td>
            </tr>
            {members.map((m) => (
              <tr key={m.id}>
                <td>
                  <input
                    type="radio"
                    name={`rep-${group.id}`}
                    aria-label={`Representative: ${nodeName(m)}`}
                    defaultChecked={group.representative_id === m.id}
                    onChange={() => update({ representative_id: m.id })}
                  />
                </td>
                <td>
                  <button className="link" onClick={() => onSelectNode(m.id)}>
                    {nodeName(m)}
                  </button>
                </td>
                <td className="muted">{m.branch_id ? branchName(byId.get(m.branch_id)) : '—'}</td>
                {energy.view && (
                  <td className="mono" title={energy.view.values[m.id]?.message ?? undefined}>
                    {formatDelta(
                      valueOf(m.id) !== null && lowest !== null ? (valueOf(m.id) as number) - lowest : null,
                      energy.settings,
                    )}
                  </td>
                )}
                <td>
                  <button
                    className="small icon danger"
                    aria-label={`Remove ${nodeName(m)} from the group`}
                    title="Remove from the group (deletes the node)"
                    onClick={() => setRemovingMember(m)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <Notes key={group.notes} notes={group.notes} onSave={(notes) => update({ notes })} />
      {removing && <RemoveGroupDialog group={group} onClose={() => setRemoving(false)} onDone={onRemoved} />}
      {removingMember && (
        <RemoveMemberDialog
          member={removingMember}
          canvas={canvas}
          onClose={() => setRemovingMember(null)}
          onDone={() => {
            setRemovingMember(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

/** WF-07: reconnect the selected nodes as a group, optionally with an outgoing branch. */
function ReconnectDialog({
  nodes,
  canvas,
  onClose,
  onDone,
}: {
  nodes: Node[]
  canvas: Canvas
  onClose: () => void
  onDone: (group: Group) => void
}) {
  const [label, setLabel] = useState('')
  const [outgoing, setOutgoing] = useState(true)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const incoming = [...new Set(nodes.map((n) => n.branch_id).filter((b): b is string => !!b))]
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  return (
    <Modal
      title="Reconnect as group"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            onClick={() =>
              api
                .reconnect(
                  nodes.map((n) => n.id),
                  label,
                  outgoing ? { name } : null,
                )
                .then(onDone, (err: unknown) => setError(errorText(err)))
            }
          >
            Reconnect
          </button>
        </>
      }
    >
      <p>
        {nodes.length} nodes become members of a new group. Each keeps its branch (D66), and their branches are
        recorded as the group's incoming branches.
      </p>
      <p className="muted small">
        Incoming branches: {incoming.length ? incoming.map((id) => branchName(byId.get(id))).join(', ') : 'none'}
      </p>
      <label className="field">
        <span>Group label</span>
        <input aria-label="Group label" value={label} onChange={(event) => setLabel(event.target.value)} />
      </label>
      <label className="check">
        <input type="checkbox" checked={outgoing} onChange={(event) => setOutgoing(event.target.checked)} />
        <span>Create an outgoing branch whose parents are the incoming branches</span>
      </label>
      {outgoing && (
        <label className="field">
          <span>Outgoing branch name</span>
          <input aria-label="Outgoing branch name" value={name} onChange={(event) => setName(event.target.value)} />
        </label>
      )}
      <p className="muted small">Transitions into the group are optional (D14).</p>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** FR-3D-04, WF-09: the second node superposed on the first after alignment. */
export function OverlayDialog({ first, second, onClose }: { first: Node; second: Node; onClose: () => void }) {
  const [overlay, setOverlay] = useState<Overlay | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.overlay(first.id, second.id).then(setOverlay, (err: unknown) => setError(errorText(err)))
  }, [first.id, second.id])
  return (
    <Modal title="Overlay in 3D" onClose={onClose} actions={<button onClick={onClose}>Close</button>}>
      <p className="overlay-legend">
        <span>
          <span className="swatch" style={{ background: '#707070' }} /> {nodeName(first)} (element colours)
        </span>
        <span>
          <span className="swatch" style={{ background: '#d6336c' }} /> {nodeName(second)}
        </span>
      </p>
      {overlay && (
        <>
          <p aria-label="Overlay result">
            {overlay.aligned
              ? `Aligned: RMSD ${overlay.rmsd!.toFixed(3)} Å`
              : 'The atoms differ in element or order, so the structures are only centred on each other, not rotated.'}
          </p>
          <div className="overlay-viewer">
            <Viewer3D models={[{ xyz: overlay.reference_xyz }, { xyz: overlay.moving_xyz, colour: '#d6336c' }]} />
          </div>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** FR-GRP-06, A20: add the selected nodes to an existing group. */
function AddToGroupDialog({
  group,
  nodes,
  canvas,
  onClose,
  onDone,
}: {
  group: Group
  nodes: Node[]
  canvas: Canvas
  onClose: () => void
  onDone: (group: Group) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const groupNames = new Map(canvas.groups.map((g) => [g.id, g.label || 'Group']))
  const moving = nodes.filter((n) => n.group_id)
  const title = group.label || 'Group'
  return (
    <Modal
      title="Add to group"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            onClick={() =>
              api.addToGroup(group.id, nodes.map((n) => n.id)).then(onDone, (err: unknown) => setError(errorText(err)))
            }
          >
            Add {nodes.length} node{nodes.length === 1 ? '' : 's'}
          </button>
        </>
      }
    >
      <p>
        {nodes.map(nodeName).join(', ')} {nodes.length === 1 ? 'becomes a member' : 'become members'} of “{title}”.
        {nodes.length === 1 ? 'It keeps its branch' : 'Each keeps its branch'} (D66), which joins the group's incoming
        branches.
      </p>
      {moving.length > 0 && (
        <p className="muted small">
          Moved from another group:{' '}
          {moving.map((n) => `${nodeName(n)} (from “${groupNames.get(n.group_id!) ?? 'Group'}”)`).join(', ')}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

function describeSelection(nodes: Node[], groups: Group[]): string {
  const parts = []
  if (nodes.length) parts.push(`${nodes.length} node${nodes.length === 1 ? '' : 's'}`)
  if (groups.length) parts.push(`${groups.length} group${groups.length === 1 ? '' : 's'}`)
  return `${parts.join(' and ')} selected`
}

/** Actions on several selected nodes: assign step or branch, reconnect, overlay two, or add
 * them to a group selected with them (A20). */
export function SelectionInspector({
  nodes,
  groups,
  canvas,
  onChanged,
  onGroupCreated,
}: {
  nodes: Node[]
  groups: Group[]
  canvas: Canvas
  onChanged: () => void
  onGroupCreated: (group: Group) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [adding, setAdding] = useState(false)
  const [overlay, setOverlay] = useState(false)
  const assign = (fields: { step_id?: string | null; branch_id?: string | null }) =>
    Promise.all(nodes.map((n) => api.updateNode(n.id, fields))).then(
      () => {
        setError(null)
        onChanged()
      },
      (err: unknown) => {
        setError(errorText(err))
        onChanged()
      },
    )
  const free = nodes.filter((n) => !n.group_id)
  const target = groups.length === 1 ? groups[0] : null
  const joining = target ? nodes.filter((n) => n.group_id !== target.id) : []
  return (
    <div className="inspector" aria-label="Selection inspector">
      <div className="inspector-head">
        <h2>{describeSelection(nodes, groups)}</h2>
      </div>
      <ul className="plain">
        {groups.map((g) => (
          <li key={g.id}>Group “{g.label || 'Group'}”</li>
        ))}
        {nodes.map((n) => (
          <li key={n.id}>{nodeName(n)}</li>
        ))}
      </ul>
      {nodes.length > 0 && (
        <section className="fields" aria-label="Assign">
          <label className="field">
            <span>Set step</span>
            <select aria-label="Set step" value="" onChange={(event) => assign({ step_id: event.target.value === 'none' ? null : event.target.value })}>
              <option value="" disabled>
                Choose…
              </option>
              <option value="none">No step</option>
              {canvas.steps.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || 'Unnamed step'}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Set branch</span>
            <select
              aria-label="Set branch"
              value=""
              onChange={(event) => assign({ branch_id: event.target.value === 'none' ? null : event.target.value })}
            >
              <option value="" disabled>
                Choose…
              </option>
              <option value="none">No branch</option>
              {canvas.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {branchName(b)}
                </option>
              ))}
            </select>
          </label>
        </section>
      )}
      <div className="buttons">
        {groups.length > 0 && (
          <button className="primary" disabled={!target || joining.length === 0} onClick={() => setAdding(true)} title="A20">
            {target ? `Add to “${target.label || 'Group'}”…` : 'Add to group…'}
          </button>
        )}
        <button
          disabled={nodes.length < 2 || groups.length > 0 || free.length !== nodes.length}
          onClick={() => setReconnecting(true)}
          title="WF-07"
        >
          Reconnect as group…
        </button>
        <button disabled={nodes.length !== 2 || nodes.some((n) => !n.xyz)} onClick={() => setOverlay(true)}>
          Overlay in 3D
        </button>
      </div>
      {groups.length === 0 && free.length !== nodes.length && (
        <p className="muted small">Some selected nodes are already in a group.</p>
      )}
      {groups.length > 1 && <p className="muted small">Select one group to add nodes to it.</p>}
      {target && nodes.length > 0 && joining.length === 0 && (
        <p className="muted small">The selected nodes are already in this group.</p>
      )}
      {error && <p role="alert">{error}</p>}
      {reconnecting && (
        <ReconnectDialog
          nodes={nodes}
          canvas={canvas}
          onClose={() => setReconnecting(false)}
          onDone={(group) => {
            setReconnecting(false)
            onGroupCreated(group)
          }}
        />
      )}
      {adding && target && (
        <AddToGroupDialog
          group={target}
          nodes={joining}
          canvas={canvas}
          onClose={() => setAdding(false)}
          onDone={(group) => {
            setAdding(false)
            onGroupCreated(group)
          }}
        />
      )}
      {overlay && nodes.length === 2 && <OverlayDialog first={nodes[0]} second={nodes[1]} onClose={() => setOverlay(false)} />}
    </div>
  )
}

/** FR-BR-02: split a node's branch into new child branches. */
export function SplitDialog({
  node,
  branch,
  onClose,
  onDone,
}: {
  node: Node
  branch: Branch
  onClose: () => void
  onDone: (created: Branch[]) => void
}) {
  const base = branch.name || 'Branch'
  const [names, setNames] = useState([`${base}1`, `${base}2`])
  const [error, setError] = useState<string | null>(null)
  return (
    <Modal
      title="Split into branches"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={names.length === 0}
            onClick={() =>
              api.splitNode(node.id, names.map((name) => ({ name }))).then(onDone, (err: unknown) => setError(errorText(err)))
            }
          >
            Create {names.length} branch{names.length === 1 ? '' : 'es'}
          </button>
        </>
      }
    >
      <p>
        New branches start at “{nodeName(node)}” with “{base}” as their parent. The node stays in “{base}”; assign later
        nodes to the new branches.
      </p>
      {names.map((name, i) => (
        <div key={i} className="add-row">
          <input
            aria-label={`New branch ${i + 1}`}
            value={name}
            onChange={(event) => setNames(names.map((n, j) => (j === i ? event.target.value : n)))}
          />
          <button className="small" aria-label={`Remove branch ${i + 1}`} onClick={() => setNames(names.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <button className="small" onClick={() => setNames([...names, `${base}${names.length + 1}`])}>
        + Another branch
      </button>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}
