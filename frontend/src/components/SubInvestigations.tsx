import { useEffect, useState } from 'react'
import { api, type Canvas, type Node, type SubDeletePreview, type SubInvestigation, type TransitionSides } from '../api'
import { Modal } from './Modal'
import { Notes, TextField } from './Fields'
import { Icon } from '../icons'
import { useRequest, type Request } from '../commands'
import { canvasName, canvasOf, chainOf, contentsText, isWithin, nodesInside, subName, subsOf } from '../subCanvas'

// D193: sub-investigations, boxes holding a canvas of their own: the box's panel, making one
// from a selection, Move to…, the inner node an arrow drawn to a closed box ends at, and Remove.

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const nodeName = (n: Node) => n.label || 'Untitled node'

/** What a list of ids stands for: nodes, groups and boxes; and the group members listed
 * without their group, which move the whole group (Q60). */
function selected(canvas: Canvas, ids: string[]) {
  const listed = new Set(ids)
  const nodes = canvas.nodes.filter((n) => listed.has(n.id))
  const groups = canvas.groups.filter((g) => listed.has(g.id))
  const subs = subsOf(canvas).filter((s) => listed.has(s.id))
  const loneMembers = nodes.filter((n) => n.group_id && !listed.has(n.group_id))
  const wholeGroups = canvas.groups.filter((g) => loneMembers.some((n) => n.group_id === g.id))
  return { nodes, groups, subs, loneMembers, wholeGroups }
}

function GroupsMoveNote({ groups }: { groups: { label: string }[] }) {
  if (!groups.length) return null
  return (
    <p className="notice small">
      A group and its members always share one canvas, so{' '}
      {groups.map((g, i) => (
        <span key={i}>
          {i > 0 && ', '}the whole group “{g.label || 'Group'}”
        </span>
      ))}{' '}
      {groups.length === 1 ? 'moves' : 'move'} with {groups.length === 1 ? 'its member' : 'their members'} (Q60).
    </p>
  )
}

/** "Move into new sub-investigation…": a name and a face; the box goes where they were. */
export function NewSubDialog({
  canvas,
  ids,
  onClose,
  onDone,
}: {
  canvas: Canvas
  ids: string[]
  onClose: () => void
  onDone: (sub: SubInvestigation) => void
}) {
  const { nodes, groups, subs, wholeGroups } = selected(canvas, ids)
  // The face: a node moving in (a group's members, a box's nodes too); the first one by default.
  const candidates = [
    ...nodes,
    ...[...groups, ...wholeGroups].flatMap((g) =>
      canvas.nodes.filter((n) => n.group_id === g.id && !nodes.includes(n)),
    ),
    ...subs.flatMap((s) => nodesInside(canvas, s.id)),
  ].filter((n, i, all) => all.indexOf(n) === i)
  const [label, setLabel] = useState('')
  const [face, setFace] = useState(candidates[0]?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const count = nodes.length + groups.length + subs.length
  const create = () =>
    api
      .createSubInvestigation({ label: label.trim(), ids, face_id: face || null, with_groups: wholeGroups.length > 0 })
      .then(onDone, (err: unknown) => setError(errorText(err)))
  return (
    <Modal
      title="Move into new sub-investigation"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={create}>
            Create
          </button>
        </>
      }
    >
      <p>
        {count} {count === 1 ? 'item moves' : 'items move'} into a box with a canvas of its own, drawn where they were.
        Their edges stay: an edge to something outside is drawn to the box.
      </p>
      <label className="field">
        <span>Name</span>
        <input
          aria-label="Sub-investigation name"
          value={label}
          autoFocus
          placeholder="e.g. Ru-14e rotations"
          onChange={(event) => setLabel(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void create()
          }}
        />
      </label>
      {candidates.length > 0 && (
        <label className="field">
          <span>Face</span>
          <select aria-label="Face" value={face} onChange={(event) => setFace(event.target.value)}>
            {candidates.map((n) => (
              <option key={n.id} value={n.id}>
                {nodeName(n)}
              </option>
            ))}
          </select>
        </label>
      )}
      <p className="muted small">
        The face is the structure the closed box shows. It is only a picture: the box has no energy, and pathways go
        through the nodes inside (D193).
      </p>
      <GroupsMoveNote groups={wholeGroups} />
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** "Move to…": onto another sub-investigation's canvas, or the main canvas. */
export function MoveToDialog({
  canvas,
  ids,
  onClose,
  onDone,
}: {
  canvas: Canvas
  ids: string[]
  onClose: () => void
  onDone: (target: string | null) => void
}) {
  const { nodes, groups, subs: moving, wholeGroups } = selected(canvas, ids)
  const subs = subsOf(canvas)
  const here = new Set([...nodes, ...groups, ...moving].map((r) => canvasOf(canvas, r.id) ?? null))
  // A box cannot go into itself or a box it holds (Q61).
  const targets: (string | null)[] = [
    null,
    ...subs.filter((s) => !moving.some((m) => isWithin(subs, s.id, m.id))).map((s) => s.id),
  ]
  const [target, setTarget] = useState<string | null | undefined>(targets.find((t) => !here.has(t)))
  const [error, setError] = useState<string | null>(null)
  const label = (id: string | null) =>
    id === null
      ? 'Main canvas'
      : chainOf(subs, id)
          .map(subName)
          .join(' › ')
  const go = () => {
    if (target === undefined) return
    api.moveToCanvas(ids, target, wholeGroups.length > 0).then(
      () => onDone(target),
      (err: unknown) => setError(errorText(err)),
    )
  }
  const count = nodes.length + groups.length + moving.length
  return (
    <Modal
      title="Move to"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={target === undefined} onClick={go}>
            Move
          </button>
        </>
      }
    >
      <p>
        Move {count} {count === 1 ? 'item' : 'items'} onto another canvas. Edges stay; on a canvas that holds something
        they keep their arrangement, placed to its right.
      </p>
      <div role="radiogroup" aria-label="Move to" className="move-targets">
        {targets.map((id) => (
          <label key={id ?? 'main'} className="check">
            <input
              type="radio"
              name="move-target"
              checked={target === id}
              disabled={here.size === 1 && here.has(id)}
              onChange={() => setTarget(id)}
            />
            <span>
              {label(id)}
              {here.size === 1 && here.has(id) && <span className="muted"> (where they are)</span>}
            </span>
          </label>
        ))}
      </div>
      <GroupsMoveNote groups={wholeGroups} />
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** An arrow drawn to or from a closed box ends at a node inside it (Q59): which one? */
export function BoxEndDialog({
  canvas,
  source,
  target,
  sides,
  onClose,
  onDone,
}: {
  canvas: Canvas
  source: string
  target: string
  sides: TransitionSides
  onClose: () => void
  onDone: () => void
}) {
  const subs = subsOf(canvas)
  const isBox = (id: string) => subs.some((s) => s.id === id)
  // The nodes and groups inside, at any depth; a group's members are reached through it.
  const inside = (box: string) => [
    ...canvas.groups
      .filter((g) => isWithin(subs, g.canvas_id ?? null, box))
      .map((g) => ({ id: g.id, label: `${g.label || 'Group'} (group)` })),
    ...canvas.nodes
      .filter((n) => !n.group_id && !n.on_edge && isWithin(subs, n.canvas_id ?? null, box))
      .map((n) => ({ id: n.id, label: nodeName(n) + (n.role === 'transition_state' ? ' (TS)' : '') })),
  ]
  const [ends, setEnds] = useState({
    source: isBox(source) ? (inside(source)[0]?.id ?? '') : source,
    target: isBox(target) ? (inside(target)[0]?.id ?? '') : target,
  })
  const [error, setError] = useState<string | null>(null)
  const pick = (which: 'source' | 'target', box: string) => (
    <label className="field">
      <span>{which === 'source' ? 'Starts at' : 'Ends at'}</span>
      <select
        aria-label={which === 'source' ? 'Starts at' : 'Ends at'}
        value={ends[which]}
        onChange={(event) => setEnds({ ...ends, [which]: event.target.value })}
      >
        {inside(box).map((r) => (
          <option key={r.id} value={r.id}>
            {r.label}
          </option>
        ))}
      </select>
    </label>
  )
  return (
    <Modal
      title="Draw the transition to a node inside"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!ends.source || !ends.target || ends.source === ends.target}
            onClick={() =>
              api.createTransition(ends.source, ends.target, sides).then(onDone, (err: unknown) => setError(errorText(err)))
            }
          >
            Create transition
          </button>
        </>
      }
    >
      <p>
        A sub-investigation is not a node: a transition ends at a real node inside it, and is drawn to the box while it
        is closed.
      </p>
      {isBox(source) && pick('source', source)}
      {isBox(target) && pick('target', target)}
      {(isBox(source) ? inside(source) : inside(target)).length === 0 && (
        <p className="muted">There is nothing inside to join.</p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** Removing a box: dissolve it (everything back where the box was), or delete it with
 * everything inside after listing what goes (as D54). An empty box is just deleted. */
function RemoveSubDialog({
  sub,
  canvas,
  onClose,
  onDone,
}: {
  sub: SubInvestigation
  canvas: Canvas
  onClose: () => void
  onDone: () => void
}) {
  const [preview, setPreview] = useState<SubDeletePreview | null>(null)
  const [choice, setChoice] = useState<'dissolve' | 'delete'>('dissolve')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.subDeletePreview(sub.id).then(setPreview, (err: unknown) => setError(errorText(err)))
  }, [sub.id])
  const empty = preview !== null && preview.nodes + preview.groups + preview.sub_investigations === 0
  const go = () =>
    (choice === 'dissolve' && !empty ? api.dissolveSubInvestigation(sub.id) : api.deleteSubInvestigation(sub.id)).then(
      onDone,
      (err: unknown) => setError(errorText(err)),
    )
  const parent = canvasName(subsOf(canvas), sub.parent_id)
  const many = (n: number, one: string, more = `${one}s`) => `${n} ${n === 1 ? one : more}`
  return (
    <Modal
      title="Remove sub-investigation?"
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="danger" disabled={!preview} onClick={go}>
            {empty ? 'Delete sub-investigation' : choice === 'dissolve' ? 'Dissolve' : 'Delete with contents'}
          </button>
        </>
      }
    >
      {preview && empty && <p>“{subName(sub)}” is empty; it is deleted.</p>}
      {preview && !empty && (
        <div className="remove-group">
          <label className="check">
            <input type="radio" name="remove-sub" checked={choice === 'dissolve'} onChange={() => setChoice('dissolve')} />
            <span>
              <strong>Dissolve</strong>: what it holds goes back onto {parent === 'Main canvas' ? 'the main canvas' : `“${parent}”`}, where the
              box was. Nothing is deleted.
            </span>
          </label>
          <label className="check">
            <input type="radio" name="remove-sub" checked={choice === 'delete'} onChange={() => setChoice('delete')} />
            <span>
              <strong>Delete with contents</strong>: this deletes
            </span>
          </label>
          {choice === 'delete' && (
            <ul aria-label="Deleted with the sub-investigation">
              <li>
                {many(preview.nodes, 'node')}
                {preview.transition_states > 0 && ` (${many(preview.transition_states, 'TS', 'TSs')})`} with{' '}
                {many(preview.calculations, 'calculation')}
              </li>
              {preview.groups > 0 && <li>{many(preview.groups, 'group')}</li>}
              {preview.sub_investigations > 0 && <li>{many(preview.sub_investigations, 'sub-investigation')} inside it</li>}
              <li>{many(preview.transitions, 'transition')} touching any of them</li>
            </ul>
          )}
          <p className="muted small">Either way the change is recorded in the investigation history.</p>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** D193: the panel of a sub-investigation's box: its name, face and notes, what it holds, and
 * how to open it (Expand here, or Open its canvas). */
export function SubInspector({
  sub,
  canvas,
  expanded,
  onChanged,
  onRemoved,
  onOpen,
  onToggle,
  onSelect,
  onMoveTo,
  asked,
}: {
  sub: SubInvestigation
  canvas: Canvas
  expanded: boolean
  onChanged: () => void
  onRemoved: () => void
  /** Open a canvas: this box's, or the one it sits on. */
  onOpen: (id: string | null) => void
  onToggle: (id: string) => void
  /** Select a node, group or box shown in the contents list. */
  onSelect: (id: string) => void
  onMoveTo: (ids: string[]) => void
  asked?: Request | null
}) {
  const [error, setError] = useState<string | null>(null)
  const [removing, setRemoving] = useState(false)
  useRequest(asked, sub.id, (action) => {
    if (action === 'remove') setRemoving(true)
  })
  const update = (fields: Parameters<typeof api.updateSubInvestigation>[1]) =>
    api.updateSubInvestigation(sub.id, fields).then(onChanged, (err: unknown) => setError(errorText(err)))
  const subs = subsOf(canvas)
  const faces = nodesInside(canvas, sub.id)
  const direct = [
    ...subs.filter((s) => s.parent_id === sub.id).map((s) => ({ id: s.id, label: subName(s), kind: 'Sub-investigation' })),
    ...canvas.groups
      .filter((g) => g.canvas_id === sub.id)
      .map((g) => ({ id: g.id, label: g.label || 'Group', kind: `Group of ${g.member_ids.length}` })),
    ...canvas.nodes
      .filter((n) => n.canvas_id === sub.id && !n.group_id)
      .map((n) => ({ id: n.id, label: nodeName(n), kind: n.role === 'transition_state' ? 'TS' : 'Node' })),
  ]
  return (
    <div className="inspector" aria-label="Sub-investigation inspector">
      <div className="inspector-head">
        <h2>
          <Icon name="layers" /> {subName(sub)}
        </h2>
        <span className="spacer" />
        <button onClick={() => onToggle(sub.id)}>{expanded ? 'Collapse' : 'Expand here'}</button>
        <button className="primary" onClick={() => onOpen(sub.id)}>
          <Icon name="enter" />
          Open
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      <p className="muted small">
        A side mechanism with a canvas of its own. The box has no energy; edges and pathways go to the nodes inside
        (D193).
      </p>
      <section className="fields" aria-label="Details">
        <TextField key={sub.label} label="Name" value={sub.label} onCommit={(label) => update({ label })} />
        <label className="field">
          <span>Face</span>
          <select
            aria-label="Face"
            value={sub.face_id ?? ''}
            onChange={(event) => update({ face_id: event.target.value || null })}
          >
            <option value="">None</option>
            {faces.map((n) => (
              <option key={n.id} value={n.id}>
                {nodeName(n)}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>On</span>
          <span>
            <button className="link" onClick={() => onOpen(sub.parent_id)}>
              {canvasName(subs, sub.parent_id)}
            </button>
          </span>
        </div>
        <div className="field">
          <span>Holds</span>
          <span>{contentsText(sub)}</span>
        </div>
      </section>
      <section aria-label="Contents">
        <h3>Inside ({direct.length})</h3>
        {direct.length === 0 ? (
          <p className="muted">Nothing yet. Use Move to… on a node, group or selection to put it here.</p>
        ) : (
          <ul className="sub-contents">
            {direct.map((r) => (
              <li key={r.id}>
                <button className="link" onClick={() => onSelect(r.id)}>
                  {r.label}
                </button>{' '}
                <span className="muted small">{r.kind}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="buttons">
          <button onClick={() => onMoveTo([sub.id])}>Move to…</button>
          <button className="danger" onClick={() => setRemoving(true)}>
            Remove…
          </button>
        </div>
      </section>
      <Notes key={sub.notes} notes={sub.notes} onSave={(notes) => update({ notes })} />
      {removing && <RemoveSubDialog sub={sub} canvas={canvas} onClose={() => setRemoving(false)} onDone={onRemoved} />}
    </div>
  )
}
