import { useState } from 'react'
import { api, ApiError, type SyncConflict, type SyncStatus } from '../api'
import { Modal } from './Modal'

// Git sync of an investigation through a private repository (D71). The backend runs git;
// these dialogs only ask and show what it answers.

const LABELS: Record<SyncStatus['state'], string> = {
  not_linked: 'Not linked',
  no_git: 'Git missing',
  up_to_date: 'Up to date',
  not_pushed: 'Not pushed',
  behind: 'Newer on GitHub',
  diverged: 'Both changed',
  unreachable: 'Offline',
  error: 'Sync failed',
}

function errorText(err: unknown): string {
  return err instanceof ApiError || err instanceof Error ? err.message : String(err)
}

function when(iso: string | undefined): string {
  return iso ? new Date(iso).toLocaleString() : 'unknown'
}

function SignInHelp() {
  return (
    <p className="muted small">
      Sign-in is left to Git; the app never sees or stores a password. On Windows, Git for Windows opens a GitHub
      sign-in in the browser the first time. On Linux or WSL, run <code>gh auth login</code> and then{' '}
      <code>gh auth setup-git</code> once.
    </p>
  )
}

/** The header button: pushes now and shows the last known state (FR-SYNC-05). */
export function SyncButton({ status, busy, onSync }: { status: SyncStatus | null; busy: boolean; onSync: () => void }) {
  const state = status?.state
  return (
    <button
      className={`sync-button${state ? ` ${state}` : ''}`}
      title={status ? `${status.message}${status.remote ? ` (${status.remote})` : ''}` : 'Commit and push now'}
      aria-label="Sync with GitHub"
      disabled={busy}
      onClick={onSync}
    >
      {busy ? 'Syncing…' : state ? `Sync · ${LABELS[state]}` : 'Sync'}
    </button>
  )
}

/** FR-SYNC-02: link the open investigation to an empty private repository. */
export function LinkDialog({ onCancel, onLinked }: { onCancel: () => void; onLinked: (status: SyncStatus) => void }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const link = () => {
    setBusy(true)
    setError(null)
    api.linkRepository(url).then(onLinked, (err: unknown) => {
      setBusy(false)
      setError(errorText(err))
    })
  }
  return (
    <Modal
      title="Sync with GitHub"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={busy || !url.trim()} onClick={link}>
            {busy ? 'Linking…' : 'Link and push'}
          </button>
        </>
      }
    >
      <p>
        Create an <strong>empty private</strong> repository on GitHub (no README), and paste its address here. The
        investigation is then pulled when you open it and pushed when you close it or press Sync.
      </p>
      <label className="field">
        <span>Repository address</span>
        <input
          aria-label="Repository address"
          value={url}
          placeholder="https://github.com/your-name/ru-caac-metathesis.git"
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && url.trim() && !busy) link()
          }}
        />
      </label>
      <SignInHelp />
      <p className="muted small">
        Keep the repository private: it holds your structures and output files. Use the same Chembook3D version on
        every computer.
      </p>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

function repositoryName(url: string): string {
  const last = url.trim().replace(/[/\\]+$/, '').split(/[/\\:]/).pop() ?? ''
  return last.replace(/\.git$/, '')
}

/** FR-SYNC-03, first step of Open from GitHub: the address. The folder is chosen next. */
export function CloneDialog({
  onCancel,
  onNext,
}: {
  onCancel: () => void
  onNext: (url: string, name: string) => void
}) {
  const [url, setUrl] = useState('')
  const next = () => url.trim() && onNext(url.trim(), repositoryName(url))
  return (
    <Modal
      title="Open from GitHub"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={!url.trim()} onClick={next}>
            Choose folder…
          </button>
        </>
      }
    >
      <p>Paste the address of the repository that holds the investigation. It is copied into a new folder.</p>
      <label className="field">
        <span>Repository address</span>
        <input
          aria-label="Repository address"
          value={url}
          placeholder="https://github.com/your-name/ru-caac-metathesis.git"
          onChange={(event) => setUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') next()
          }}
        />
      </label>
      <SignInHelp />
    </Modal>
  )
}

/** FR-SYNC-06: both sides changed. Nothing is merged; the user keeps one copy. */
export function ConflictDialog({
  conflict,
  onCancel,
  onKeep,
}: {
  conflict: SyncConflict
  onCancel: () => void
  onKeep: (keep: 'this' | 'github') => void
}) {
  const [busy, setBusy] = useState(false)
  const keep = (choice: 'this' | 'github') => {
    setBusy(true)
    onKeep(choice)
  }
  return (
    <Modal
      title="This computer and GitHub both have changes"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel} disabled={busy}>
            Not now
          </button>
          <button disabled={busy} onClick={() => keep('github')}>
            Keep the GitHub copy
          </button>
          <button disabled={busy} onClick={() => keep('this')}>
            Keep this computer's copy
          </button>
        </>
      }
    >
      <p>
        <code>{conflict.folder}</code> was changed here and on another computer since the last sync. The two copies
        cannot be merged, so keep one of them.
      </p>
      <table className="conflict">
        <tbody>
          <tr>
            <th scope="row">This computer</th>
            <td>{when(conflict.local?.when)}</td>
            <td className="muted">{conflict.local?.summary}</td>
          </tr>
          <tr>
            <th scope="row">GitHub</th>
            <td>{when(conflict.upstream?.when)}</td>
            <td className="muted">{conflict.upstream?.summary}</td>
          </tr>
        </tbody>
      </table>
      <p className="muted small">
        Nothing is lost. Keeping this computer's copy saves the GitHub database in <code>backups/</code> and keeps it
        in the repository's history. Keeping the GitHub copy saves this computer's database in <code>backups/</code>{' '}
        and its changes on a <code>set-aside/…</code> branch.
      </p>
    </Modal>
  )
}

/** FR-SYNC-07: upgrading a linked investigation locks out older versions elsewhere. */
export function UpgradeDialog({ folder, onCancel, onUpgrade }: { folder: string; onCancel: () => void; onUpgrade: () => void }) {
  return (
    <Modal
      title="Upgrade this investigation?"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" onClick={onUpgrade}>
            Upgrade and open
          </button>
        </>
      }
    >
      <p>
        <code>{folder}</code> was saved by an older version of Chembook3D. Opening it upgrades the database (a backup is
        kept in <code>backups/</code>).
      </p>
      <p>
        After the next sync, older Chembook3D versions on your other computers cannot open it until they are updated
        too.
      </p>
    </Modal>
  )
}
