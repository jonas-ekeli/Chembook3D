// D107 (FR-RUN-04): the Shut down button, the page a tab shows once the server has stopped, and
// the launcher's notices.

import { useState, type ReactNode } from 'react'
import { api, type ShutdownInfo } from '../api'
import { shutDown, useLifecycle, type Stopped } from '../lifecycle'
import { Modal } from './Modal'

export function ShutDownButton() {
  const [info, setInfo] = useState<ShutdownInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const ask = () => {
    setError(null)
    api.shutdownInfo().then(setInfo, (err: unknown) => setError(String(err)))
  }
  const confirm = () => {
    setBusy(true)
    shutDown().catch((err: unknown) => {
      setBusy(false)
      setError(err instanceof Error ? err.message : String(err))
    })
  }

  return (
    <>
      <button onClick={ask} title="Close the investigation and stop Chembook3D">
        Shut down
      </button>
      {(info || error) && (
        <Modal
          title="Shut down Chembook3D?"
          onClose={() => {
            if (busy) return
            setInfo(null)
            setError(null)
          }}
          actions={
            <>
              <button
                onClick={() => {
                  setInfo(null)
                  setError(null)
                }}
                disabled={busy}
              >
                Cancel
              </button>
              <button className="danger" onClick={confirm} disabled={busy || !info}>
                {busy ? (info?.linked ? 'Pushing and shutting down…' : 'Shutting down…') : 'Shut down'}
              </button>
            </>
          }
        >
          {info && (
            <>
              <p>
                {info.investigation
                  ? info.linked
                    ? 'The investigation is closed and pushed to GitHub, then Chembook3D stops.'
                    : 'The investigation is closed, then Chembook3D stops.'
                  : 'Chembook3D stops.'}
              </p>
              {info.claude_panel && (
                <p>
                  The conversation in the Claude panel stops too. “Continue last conversation” picks it up
                  next time.
                </p>
              )}
              <p className="muted">
                {info.launched ? (
                  'Start it again with the Chembook3D shortcut.'
                ) : (
                  <>
                    Start it again with <code>uv run chembook3d</code>.
                  </>
                )}
              </p>
            </>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </Modal>
      )}
    </>
  )
}

/** The app, until the server has stopped; then the tab says so instead. */
export function UntilStopped({ children }: { children: ReactNode }) {
  const { stopped } = useLifecycle()
  return stopped ? <StoppedPage stopped={stopped} /> : children
}

/** What a tab shows once the server has stopped. */
function StoppedPage({ stopped }: { stopped: Stopped }) {
  return (
    <main className="start">
      <h1>{stopped.how === 'shutdown' ? 'Chembook3D has shut down' : 'Chembook3D is not running'}</h1>
      {stopped.how === 'shutdown' ? (
        <p>You can close this tab.</p>
      ) : (
        <p>
          It was stopped, or the computer went to sleep. Start it again with the Chembook3D shortcut or
          <code> uv run chembook3d</code>; this tab then reconnects by itself.
        </p>
      )}
      {stopped.sync && <p className="muted">Sync: {stopped.sync}</p>}
    </main>
  )
}

const dismissed = new Set<string>() // for the whole tab, also after an investigation is opened

/** The launcher's notices (why the checkout was not updated), until dismissed. */
export function LaunchNotices() {
  const { notices } = useLifecycle()
  const [, setCount] = useState(0)
  const shown = notices.filter((n) => !dismissed.has(n))
  if (shown.length === 0) return null
  return (
    <div className="banner" role="status" aria-label="Launcher">
      {shown.join(' ')}
      <button
        className="link"
        onClick={() => {
          shown.forEach((n) => dismissed.add(n))
          setCount((c) => c + 1)
        }}
      >
        Dismiss
      </button>
    </div>
  )
}
