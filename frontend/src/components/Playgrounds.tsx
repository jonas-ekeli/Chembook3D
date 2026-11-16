// D235: playgrounds, working copies of an investigation kept in its playgrounds/ folder. The
// dialog that makes one from the start page, the band under the app bar while one is open, the
// confirmation that discards one, and the review that merges one into its original.

import { Fragment, useEffect, useMemo, useState } from 'react'
import {
  api,
  type MergeChange,
  type MergeResult,
  type MergeReview,
  type PlaygroundDiscardPreview,
  type PlaygroundInfo,
} from '../api'
import { Icon } from '../icons'
import { initialTicks, pick, setTick, unpicked, type Ticks } from '../mergeTicks'
import { byteSize, errorText, nextPlaygroundName, relativeTime } from '../util'
import { Modal } from './Modal'

export function NewPlaygroundDialog({
  original,
  folder,
  taken,
  onCancel,
  onMake,
}: {
  /** The original's name. */
  original: string
  folder: string
  /** The names of its playgrounds so far. */
  taken: string[]
  onCancel: () => void
  /** Makes it and opens it; a refusal is shown in the dialog. */
  onMake: (name: string) => Promise<void>
}) {
  const [name, setName] = useState(() => nextPlaygroundName(taken))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const make = () => {
    if (busy || !name.trim()) return
    setBusy(true)
    setError(null)
    onMake(name.trim()).then(
      () => setBusy(false),
      (err: unknown) => {
        setBusy(false)
        setError(errorText(err))
      },
    )
  }
  return (
    <Modal
      title="New playground"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={busy || !name.trim()} onClick={make}>
            {busy ? 'Making…' : 'Make and open'}
          </button>
        </>
      }
    >
      <p>
        A full working copy of <strong>{original}</strong> to try things in: its nodes, calculations, files, notes,
        analyses and canvas as they are now. The original is not changed.
      </p>
      <label className="field">
        <span>Name</span>
        <input
          aria-label="Playground name"
          value={name}
          maxLength={100}
          autoFocus
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') make()
          }}
        />
      </label>
      <p className="muted small">
        Kept in <code>{folder.replace(/[\\/]+$/, '')}/playgrounds/</code>, so it moves with the original; its files
        take no extra room on the same disk. It is never synced to GitHub.
      </p>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** D235g: Discard…, sending a playground's folder to the recycle bin once its name, folder and size
 * are shown. The original is not touched. */
export function DiscardPlaygroundDialog({
  folder,
  onCancel,
  onDiscarded,
}: {
  folder: string
  onCancel: () => void
  onDiscarded: (discarded: PlaygroundDiscardPreview) => void
}) {
  const [preview, setPreview] = useState<PlaygroundDiscardPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let current = true
    api.playgroundDiscardPreview(folder).then(
      (found) => current && setPreview(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [folder])
  const discard = () => {
    if (!preview || busy) return
    setBusy(true)
    setError(null)
    api.discardPlayground(folder).then(
      () => onDiscarded(preview),
      (err: unknown) => {
        setBusy(false)
        setError(errorText(err))
      },
    )
  }
  return (
    <Modal
      title={preview ? `Discard ${preview.name}?` : 'Discard playground?'}
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="danger" disabled={!preview || busy} onClick={discard}>
            {busy ? 'Discarding…' : 'Discard'}
          </button>
        </>
      }
    >
      {preview ? (
        <>
          <p>
            Its folder goes to the recycle bin, with everything done in it since it was made. Its original,{' '}
            <strong>{preview.original_name}</strong>, is not changed.
          </p>
          <p className="muted small">
            <code>{preview.folder}</code>
            <br />
            {preview.files} {preview.files === 1 ? 'file' : 'files'}, {byteSize(preview.size)}
            {preview.frees < preview.size &&
              `; ${byteSize(preview.frees)} freed, since the rest are the original's files under a second name`}
            . You can restore it from the recycle bin.
          </p>
        </>
      ) : (
        !error && <p className="muted">Looking at the playground…</p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

const KIND_TEXT: Record<MergeChange['kind'], string> = {
  added: 'Added',
  changed: 'Changed',
  removed: 'Removed',
  conflict: 'Conflict',
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function ChangeRow({
  change,
  ticks,
  needs,
  onTick,
  onPick,
}: {
  change: MergeChange
  ticks: Ticks
  /** The labels of what it brings with it. */
  needs: string[]
  onTick: (on: boolean) => void
  onPick: (side: 'playground' | 'original') => void
}) {
  const conflicting = change.fields.filter((f) => f.conflict)
  return (
    <li className={`merge-change merge-${change.kind}`}>
      {change.kind === 'conflict' && !change.blocked ? (
        <div className="merge-pick" role="radiogroup" aria-label={`${change.label}: which version`}>
          <label className="check">
            <input
              type="radio"
              name={change.id}
              checked={ticks.picks[change.id] === 'playground'}
              onChange={() => onPick('playground')}
            />{' '}
            Playground's
          </label>
          <label className="check">
            <input
              type="radio"
              name={change.id}
              checked={ticks.picks[change.id] === 'original'}
              onChange={() => onPick('original')}
            />{' '}
            Original's
          </label>
        </div>
      ) : (
        <input
          type="checkbox"
          aria-label={change.label}
          checked={ticks.ticked.has(change.id)}
          disabled={!!change.blocked}
          onChange={(event) => onTick(event.target.checked)}
        />
      )}
      <div className="merge-what">
        <div>
          <span className="merge-kind">{KIND_TEXT[change.kind]}</span> <strong>{change.label}</strong>
          {/* "added" and "removed" say no more than the badge */}
          {change.summary && change.summary.toLowerCase() !== change.kind && (
            <span className="muted"> · {change.summary}</span>
          )}
        </div>
        {change.conflict && <div className="merge-reason">{change.conflict}</div>}
        {conflicting.map((f) => (
          <div key={f.field} className="muted small">
            {f.field}: playground {show(f.playground)} · original {show(f.original)} (was {show(f.base)})
          </div>
        ))}
        {change.blocked && <div className="merge-reason">{change.blocked}</div>}
        {needs.length > 0 && <div className="muted small">Brings {needs.join(', ')}</div>}
      </div>
    </li>
  )
}

/** D235h: the review of what merging a playground brings into its original: a tick box per change,
 * grouped by node, with conflicts to decide; Preview runs it without keeping anything. */
export function MergePlaygroundDialog({
  folder,
  review,
  onCancel,
  onMerged,
}: {
  folder: string
  review: MergeReview
  onCancel: () => void
  onMerged: (result: MergeResult) => void
}) {
  const changes = useMemo(() => review.sections.flatMap((s) => s.changes), [review])
  const byId = useMemo(() => new Map(changes.map((c) => [c.id, c])), [changes])
  const [ticks, setTicks] = useState<Ticks>(() => initialTicks(changes))
  const [moved, setMoved] = useState<string | null>(null)
  const [preview, setPreview] = useState<MergeResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'merge' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const waiting = unpicked(changes, ticks)
  const ticked = [...ticks.ticked]

  const update = (next: ReturnType<typeof setTick>) => {
    setTicks(next.ticks)
    setPreview(null)
    setMoved(
      next.alsoTicked.length
        ? `Also ticked, as needed: ${next.alsoTicked.join(', ')}`
        : next.alsoUnticked.length
          ? `Also unticked, as they need it: ${next.alsoUnticked.join(', ')}`
          : null,
    )
  }
  const run = (what: 'preview' | 'merge') => {
    if (busy) return
    setBusy(what)
    setError(null)
    const call = what === 'preview' ? api.previewMerge(folder, ticked) : api.mergePlayground(folder, ticked)
    call.then(
      (result) => {
        setBusy(null)
        if (what === 'merge') onMerged(result)
        else setPreview(result)
      },
      (err: unknown) => {
        setBusy(null)
        setError(errorText(err, { blockers: true }))
      },
    )
  }
  const count = ticks.ticked.size
  return (
    <Modal
      title={`Merge ${review.playground.name} into ${review.original_name}`}
      wide
      onClose={onCancel}
      note={
        waiting.length > 0
          ? `Pick a version for ${waiting.length} conflict${waiting.length === 1 ? '' : 's'} first`
          : `${count} change${count === 1 ? '' : 's'} ticked`
      }
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button disabled={busy !== null || count === 0} onClick={() => run('preview')}>
            {busy === 'preview' ? 'Checking…' : 'Preview'}
          </button>
          <button
            className="primary"
            disabled={busy !== null || count === 0 || waiting.length > 0}
            onClick={() => run('merge')}
          >
            {busy === 'merge' ? 'Merging…' : 'Merge'}
          </button>
        </>
      }
    >
      <p>
        What <strong>{review.playground.name}</strong> added, changed and removed since it was made
        {review.changed_since > 0 &&
          `; ${review.original_name} itself changed ${review.changed_since} thing${review.changed_since === 1 ? '' : 's'} since`}
        . Removals are not ticked unless you tick them. Merging saves a copy of {review.original_name} in its backups
        folder first; afterwards the playground can be opened but not changed.
      </p>
      {changes.length === 0 && <p className="muted">The playground has no changes to merge.</p>}
      {review.sections.map((section) => (
        <Fragment key={section.key}>
          <h3 className="merge-section">{section.title}</h3>
          <ul className="merge-list" aria-label={section.title}>
            {section.changes.map((change) => (
              <ChangeRow
                key={change.id}
                change={change}
                ticks={ticks}
                needs={change.requires.map((id) => byId.get(id)?.label ?? id)}
                onTick={(on) => update(setTick(changes, ticks, change.id, on))}
                onPick={(side) => update(pick(changes, ticks, change.id, side))}
              />
            ))}
          </ul>
        </Fragment>
      ))}
      {moved && <p className="muted small">{moved}</p>}
      {preview && (
        <p role="status">
          Merging would add {preview.counts.added}, change {preview.counts.changed} and remove{' '}
          {preview.counts.removed} record{preview.counts.removed === 1 ? '' : 's'}
          {preview.brought.length > 0 &&
            `, bringing ${preview.brought.map((id) => byId.get(id)?.label ?? id).join(', ')} as needed`}
          .
          {preview.blockers.length > 0 && ` It cannot be merged: ${preview.blockers.join('; ')}`}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

/** D235: says the open investigation is a playground, and of what. */
export function PlaygroundBand({
  playground,
  onOpenOriginal,
  onMerge,
  onDiscard,
}: {
  playground: PlaygroundInfo
  onOpenOriginal: () => void
  /** D235h: Merge… (opens the original and its review). */
  onMerge: () => void
  /** D235g: Discard… (asks first). */
  onDiscard: () => void
}) {
  return (
    <div className="playground-band" role="note" aria-label="Playground">
      <Icon name="playground" />
      <span>
        <strong>{playground.name}</strong> is a playground of <strong>{playground.original_name}</strong>
        {playground.created_at && (
          <span className="muted">
            {' '}
            · made{' '}
            <time dateTime={playground.created_at} title={new Date(playground.created_at).toLocaleString()}>
              {relativeTime(playground.created_at)}
            </time>
          </span>
        )}
        {playground.merged_at && (
          <span className="muted">
            {' '}
            · merged{' '}
            <time dateTime={playground.merged_at} title={new Date(playground.merged_at).toLocaleString()}>
              {relativeTime(playground.merged_at)}
            </time>
            ; read-only
          </span>
        )}
      </span>
      {!playground.original_found && (
        <span className="playground-lost">
          <Icon name="warn" />
          Its original was not found in {playground.original_folder}
        </span>
      )}
      <span className="spacer" />
      <button
        className="quiet"
        disabled={!playground.original_found}
        title={playground.original_folder}
        onClick={onOpenOriginal}
      >
        Open original
      </button>
      {!playground.merged_at && (
        <button
          className="quiet"
          disabled={!playground.original_found}
          title="Open the original and review what this playground would bring into it"
          onClick={onMerge}
        >
          Merge…
        </button>
      )}
      <button className="quiet" title="Send this playground to the recycle bin (asks first)" onClick={onDiscard}>
        Discard…
      </button>
    </div>
  )
}
