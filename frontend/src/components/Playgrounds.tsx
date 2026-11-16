// D235: playgrounds, working copies of an investigation kept in its playgrounds/ folder. The
// dialog that makes one from the start page, and the band under the app bar while one is open.

import { useState } from 'react'
import type { PlaygroundInfo } from '../api'
import { Icon } from '../icons'
import { errorText, nextPlaygroundName, relativeTime } from '../util'
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

/** D235: says the open investigation is a playground, and of what. */
export function PlaygroundBand({ playground, onOpenOriginal }: { playground: PlaygroundInfo; onOpenOriginal: () => void }) {
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
    </div>
  )
}
