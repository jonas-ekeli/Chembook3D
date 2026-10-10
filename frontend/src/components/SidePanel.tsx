import { useRef, useState } from 'react'
import { api, ApiError, type RemoteServer, type RemoteStatus } from '../api'
import { ClaudePanel } from './ClaudePanel'
import { ServerPanel } from './ServerPanel'

// The panel docked at the right of the notebook: Claude Code (D92) and a tab for each saved
// SSH server such as Saga (D122). Every tab stays mounted while another is shown or the panel
// is hidden, so Claude keeps running and a server's terminal keeps its screen.

const MIN_WIDTH = 360
const MAX_WIDTH = 1400
const NEW = '+new'

export type SideTab = string // "claude", a server's id, or NEW

function NewServer({ servers, onSaved }: { servers: RemoteServer[]; onSaved: (id: string) => void }) {
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState(22)
  const [username, setUsername] = useState(servers.find((s) => s.username)?.username ?? '')
  const [error, setError] = useState<string | null>(null)
  const save = async () => {
    setError(null)
    try {
      const saved = await api.saveServers([
        ...servers.map(({ id, name, host, port, username }) => ({ id, name, host, port, username })),
        { name: name.trim(), host: host.trim(), port, username: username.trim() },
      ])
      onSaved(saved[saved.length - 1].id)
    } catch (err) {
      setError(err instanceof ApiError && typeof err.detail === 'string' ? err.detail : String(err))
    }
  }
  return (
    <div className="claude-intro">
      <p>Another server that logs in with SSH, such as Betzy (betzy.sigma2.no) or Olivia.</p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="server-fields">
        <label className="field">
          <span>Name</span>
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label className="field">
          <span>Host</span>
          <input value={host} onChange={(event) => setHost(event.target.value)} />
        </label>
        <label className="field">
          <span>Port</span>
          <input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>User name</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
      </div>
      <div className="buttons">
        <button className="primary" disabled={!name.trim() || !host.trim()} onClick={() => void save()}>
          Add server
        </button>
      </div>
    </div>
  )
}

export function SidePanel({
  tab: requested,
  onTab,
  remote,
  onRemoteChanged,
  onImportFrom,
}: {
  /** The tab shown, or null when the panel is hidden. */
  tab: SideTab | null
  onTab: (tab: SideTab | null) => void
  remote: RemoteStatus | null
  onRemoteChanged: () => Promise<void> | void
  /** "Import from here" on a server's tab (D122e). */
  onImportFrom: (server: RemoteServer) => void
}) {
  const [width, setWidth] = useState(560)
  const resizing = useRef<{ x: number; width: number } | null>(null)
  const servers = remote?.enabled ? remote.servers : []
  // A server removed from the list while its tab was shown: show Claude instead.
  const gone = requested !== null && requested !== 'claude' && requested !== NEW && remote !== null
  const tab = gone && !servers.some((s) => s.id === requested) ? 'claude' : requested
  const current = servers.find((s) => s.id === tab)
  const label = tab === 'claude' ? 'Claude' : tab === NEW ? 'New server' : (current?.name ?? 'Side panel')
  const open = tab !== null

  return (
    <>
      <div
        className="splitter"
        hidden={!open}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the side panel"
        onPointerDown={(event) => {
          resizing.current = { x: event.clientX, width }
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={(event) => {
          if (!resizing.current) return
          const next = resizing.current.width - (event.clientX - resizing.current.x)
          setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)))
        }}
        onPointerUp={() => (resizing.current = null)}
      />
      <aside className="claude-panel" hidden={!open} style={{ width }} aria-label={label}>
        <div className="side-tabs" role="tablist" aria-label="Side panel">
          <button role="tab" aria-selected={tab === 'claude'} onClick={() => onTab('claude')}>
            Claude
          </button>
          {servers.map((server) => (
            <button
              key={server.id}
              role="tab"
              aria-selected={tab === server.id}
              onClick={() => onTab(server.id)}
              title={server.connected ? `Logged in as ${server.username}` : 'Not logged in'}
            >
              {server.name}
              {server.connected && <span className="dot" aria-label="logged in" />}
            </button>
          ))}
          {remote?.enabled && (
            <button role="tab" aria-selected={tab === NEW} title="Add an SSH server" onClick={() => onTab(NEW)}>
              +
            </button>
          )}
          <span className="spacer" />
          <button className="link" onClick={() => onTab(null)} title="Hide the panel; everything in it keeps running">
            Hide
          </button>
        </div>
        <ClaudePanel open={tab === 'claude'} onAttention={() => onTab('claude')} />
        {servers.map((server) => (
          <ServerPanel
            key={server.id}
            server={server}
            servers={servers}
            open={tab === server.id}
            onChanged={onRemoteChanged}
            onImportHere={() => onImportFrom(server)}
          />
        ))}
        {tab === NEW && (
          <section className="side-pane">
            <NewServer
              servers={servers}
              onSaved={async (id) => {
                await onRemoteChanged()
                onTab(id)
              }}
            />
          </section>
        )}
        {remote && !remote.enabled && tab !== 'claude' && (
          <p role="alert" className="error claude-intro">
            {remote.reason}
          </p>
        )}
      </aside>
    </>
  )
}
