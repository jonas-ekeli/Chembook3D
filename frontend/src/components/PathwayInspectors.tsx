import { useEffect, useRef, useState } from 'react'
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
  type Settings,
  type Side,
  type SpeciesDirection,
  type Status,
  type Transition,
} from '../api'
import { Modal } from './Modal'
import { Notes, TextField } from './Fields'
import { AtomMatchDialog } from './AtomMatchDialog'
import { ScanPathDialog } from './ScanPathDialog'
import { ScanPathsSection } from './ScanPathsOnEdge'
import { MAX_OVERLAY, OverlayDialog } from './OverlayDialog'
import { CompareStericsDialog } from './Sterics'

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
  settings,
  refreshKey,
  onChanged,
  onNotice,
  onDeleted,
  onSelectNode,
  onSelectGroup,
}: {
  transition: Transition
  canvas: Canvas
  /** The energy unit for the scan paths' tops (D121). */
  settings: Settings | null
  refreshKey: number
  onChanged: () => void
  /** D121: something changed through a scan path, with a notice to show. */
  onNotice: (notice?: string) => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
  onSelectGroup: (id: string) => void
}) {
  const paths = transition.scan_paths ?? []
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
      <ScanPathsSection
        transition={transition}
        canvas={canvas}
        settings={settings}
        refreshKey={refreshKey}
        onChanged={onNotice}
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
          {paths.length > 0 && (
            <>
              <p>These scan paths stay as nodes, unlinked, with their boxes back on the canvas (INV-7):</p>
              <ul aria-label="Scan paths kept">
                {paths.map((p) => (
                  <li key={p.node_id}>{p.label || 'Untitled path'}</li>
                ))}
              </ul>
            </>
          )}
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
  const [sterics, setSterics] = useState(false)
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
        <div className="buttons">
          <button
            disabled={!members.some((n) => n.xyz)}
            onClick={() => setSterics(true)}
            title="Buried volume and steric maps of the branch's nodes (D81)"
          >
            Compare sterics
          </button>
        </div>
      </section>
      {sterics && (
        <CompareStericsDialog
          nodes={members.filter((n) => n.xyz)}
          title={`Nodes of “${branchName(branch)}”`}
          onClose={() => setSterics(false)}
        />
      )}
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

/** D88: a member can be taken out of the group and kept, with its calculations and edges, or
 * deleted (FR-IMP-11) after a confirmation that lists what goes with it (P3). */
function RemoveMemberDialog({
  group,
  member,
  canvas,
  onClose,
  onDone,
}: {
  group: Group
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
  const last = group.member_ids.length < 2
  const representative = group.representative_id === member.id
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
          <button
            className="primary"
            disabled={last}
            onClick={() =>
              api.removeFromGroup(group.id, member.id).then(onDone, (err: unknown) => setError(errorText(err)))
            }
          >
            Keep as a node
          </button>
        </>
      }
    >
      {error && <p role="alert">{error}</p>}
      <p>
        {last
          ? `“${nodeName(member)}” is the group's last member; dissolve the group to keep it as a node.`
          : `Keep as a node: “${nodeName(member)}” leaves the group and stays beside it, with its calculations and edges${representative ? '. The group then has no representative until you pick one' : ''}.`}
      </p>
      {preview && (
        <>
          <p>
            Remove and delete: it is deleted, with its {preview.calculations} calculation
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

/** D89: the order of a group's members, which the grid, column and row layouts follow. Each
 * move is saved at once, like the order of the reaction steps (FR-STEP-01). */
export function GroupOrderDialog({
  group,
  nodes,
  onClose,
  onChanged,
}: {
  group: Group
  nodes: Node[]
  onClose: () => void
  onChanged: () => void
}) {
  const [order, setOrder] = useState(group.member_ids)
  const saving = useRef(Promise.resolve())
  const [error, setError] = useState<string | null>(null)
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const move = (index: number, by: number) => {
    const ids = [...order]
    const [moved] = ids.splice(index, 1)
    ids.splice(index + by, 0, moved)
    setOrder(ids)
    // One save at a time, so quick clicks reach the backend in the order they were made.
    saving.current = saving.current.then(() =>
      api.reorderGroup(group.id, ids).then(
        () => {
          setError(null)
          onChanged()
        },
        (err: unknown) => setError(errorText(err)),
      ),
    )
  }
  return (
    <Modal title="Member order" onClose={onClose} actions={<button onClick={onClose}>Done</button>}>
      <p className="muted small">The grid, the column and the row all follow this order.</p>
      {error && <p role="alert">{error}</p>}
      <ol className="member-order" aria-label="Members in order">
        {order.map((id, index) => {
          const name = nodeName(byId.get(id))
          return (
            <li key={id} className="step-row">
              <span className="step-number">{index + 1}</span>
              <span className="member-order-name">{name}</span>
              <button className="small icon" aria-label={`Move ${name} up`} disabled={index === 0} onClick={() => move(index, -1)}>
                ↑
              </button>
              <button
                className="small icon"
                aria-label={`Move ${name} down`}
                disabled={index === order.length - 1}
                onClick={() => move(index, 1)}
              >
                ↓
              </button>
            </li>
          )
        })}
      </ol>
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
  const [overlay, setOverlay] = useState(false)
  const [sterics, setSterics] = useState(false)
  const update = (fields: Parameters<typeof api.updateGroup>[1]) =>
    api.updateGroup(group.id, fields).then(onChanged, (err: unknown) => setError(errorText(err)))
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  const listed = group.member_ids.map((id) => canvas.nodes.find((n) => n.id === id)).filter((n): n is Node => !!n)
  // FR-GRP-03: members can be sorted by energy at the view's level and type. Sorting only
  // orders the list; the representative stays the user's choice (EN-10).
  const valueOf = (id: string) => energy.view?.values[id]?.value ?? null
  const known = listed.map((m) => valueOf(m.id)).filter((v): v is number => v !== null)
  const lowest = known.length ? Math.min(...known) : null
  // D80: the members with coordinates, the representative first so it is the reference.
  const shapes = listed
    .filter((n) => n.xyz)
    .sort((a, b) => Number(b.id === group.representative_id) - Number(a.id === group.representative_id))
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
                    title="Remove from the group, keeping or deleting the node"
                    onClick={() => setRemovingMember(m)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="buttons">
          <button
            disabled={shapes.length < 2 || shapes.length > MAX_OVERLAY}
            onClick={() => setOverlay(true)}
            title={`Superpose the members with coordinates, on the representative if there is one (FR-3D-04)`}
          >
            Overlay members
          </button>
          <button
            disabled={shapes.length < 1}
            onClick={() => setSterics(true)}
            title="Buried volume and steric maps of the members (D81)"
          >
            Compare sterics
          </button>
        </div>
        {shapes.length > MAX_OVERLAY && (
          <p className="muted small">
            An overlay holds up to {MAX_OVERLAY} structures. Select up to {MAX_OVERLAY} members on the canvas and use Overlay in 3D.
          </p>
        )}
      </section>
      <Notes key={group.notes} notes={group.notes} onSave={(notes) => update({ notes })} />
      {overlay && <OverlayDialog nodes={shapes} onClose={() => setOverlay(false)} />}
      {sterics && (
        <CompareStericsDialog nodes={shapes} title={`Members of “${group.label || 'Group'}”`} onClose={() => setSterics(false)} />
      )}
      {removing && <RemoveGroupDialog group={group} onClose={() => setRemoving(false)} onDone={onRemoved} />}
      {removingMember && (
        <RemoveMemberDialog
          group={group}
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

/** Actions on several selected nodes: assign step or branch, reconnect, overlay up to 12, or add
 * them to a group selected with them (A20). */
export function SelectionInspector({
  nodes,
  groups,
  canvas,
  onChanged,
  onGroupCreated,
  onSelectivity,
}: {
  nodes: Node[]
  groups: Group[]
  canvas: Canvas
  onChanged: () => void
  onGroupCreated: (group: Group) => void
  /** D83: compare the selected items as the outcomes of a new selectivity. */
  onSelectivity: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [adding, setAdding] = useState(false)
  const [overlay, setOverlay] = useState(false)
  const [sterics, setSterics] = useState(false)
  const [matching, setMatching] = useState(false)
  const [scanning, setScanning] = useState(false)
  const scanEnds = scanPathEnds(nodes, groups, canvas)
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
        <button
          disabled={nodes.length < 2 || nodes.length > MAX_OVERLAY || nodes.some((n) => !n.xyz)}
          onClick={() => setOverlay(true)}
          title={`2 to ${MAX_OVERLAY} nodes with coordinates (FR-3D-04)`}
        >
          Overlay in 3D
        </button>
        <button
          disabled={nodes.length !== 2 || groups.length > 0 || nodes.some((n) => !n.xyz)}
          onClick={() => setMatching(true)}
          title="Match the second structure's atoms to the first one's numbering (D113)"
        >
          Match atoms…
        </button>
        <button
          disabled={!scanEnds}
          onClick={() => setScanning(true)}
          title={
            scanEnds
              ? 'A path of xTB structures from one to the other, designed and run by a Claude Code cloud session (D114)'
              : 'Select two nodes or groups joined by an edge'
          }
        >
          Scan path…
        </button>
        <button
          disabled={nodes.filter((n) => n.xyz).length < 1}
          onClick={() => setSterics(true)}
          title="Buried volume and steric maps of the selected nodes (D81)"
        >
          Compare sterics
        </button>
        <button
          onClick={onSelectivity}
          title="ΔΔG‡ and the predicted ratio, with each selected TS or group as one outcome (D83)"
        >
          Selectivity…
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
      {overlay && nodes.length >= 2 && <OverlayDialog nodes={nodes} onClose={() => setOverlay(false)} />}
      {scanning && scanEnds && <ScanPathDialog startId={scanEnds[0]} endId={scanEnds[1]} onClose={() => setScanning(false)} />}
      {matching && nodes.length === 2 && <AtomMatchDialog nodes={[nodes[0], nodes[1]]} onClose={() => setMatching(false)} />}
      {sterics && (
        <CompareStericsDialog nodes={nodes.filter((n) => n.xyz)} title={describeSelection(nodes, [])} onClose={() => setSterics(false)} />
      )}
    </div>
  )
}

/** D114: the two selected items (nodes or groups) of a scan path, start first, when an edge
 * joins them (also through a member's group, D108); the edge's direction gives the start. */
function scanPathEnds(nodes: Node[], groups: Group[], canvas: Canvas): [string, string] | null {
  const items = [...nodes.map((n) => ({ id: n.id, ids: [n.id, n.group_id].filter(Boolean) as string[] })), ...groups.map((g) => ({ id: g.id, ids: [g.id] }))]
  if (items.length !== 2) return null
  const [a, b] = items
  const joins = (from: string[], to: string[]) => canvas.transitions.some((t) => from.includes(t.source_id) && to.includes(t.target_id))
  if (joins(a.ids, b.ids)) return [a.id, b.id]
  if (joins(b.ids, a.ids)) return [b.id, a.id]
  return null
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
