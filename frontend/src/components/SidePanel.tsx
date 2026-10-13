import { useEffect, useRef, useState } from 'react'
import { api, ApiError, serverFields, type RemoteServer, type RemoteStatus } from '../api'
import { CLUSTER_PRESETS, HPC } from '../hpc'
import { ClaudePanel } from './ClaudePanel'
import { ServerPanel } from './ServerPanel'

// The panel docked at the right of the notebook: Claude Code (D92) and the HPC servers such as
// Saga (D122). D127: one "HPC" tab chooses a saved cluster (or adds one) and logs in to it; a
// logged-in cluster gets a tab of its own named after it, and "+" opens the HPC tab again for
// another one. Every server's pane stays mounted while another is shown or the panel is hidden,
// so Claude keeps running and a server's terminal keeps its screen.

const MIN_WIDTH = 360
const MAX_WIDTH = 1400
const NEW = '+new'

export type SideTab = string // "claude", HPC, or a logged-in server's id

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
        ...servers.map(serverFields),
        { name: name.trim(), host: host.trim(), port, username: username.trim() },
      ])
      onSaved(saved[saved.length - 1].id)
    } catch (err) {
      setError(err instanceof ApiError && typeof err.detail === 'string' ? err.detail : String(err))
    }
  }
  const known = new Set(servers.map((s) => s.name.toLowerCase()))
  const presets = CLUSTER_PRESETS.filter((p) => !known.has(p.name.toLowerCase()))
  return (
    <div className="claude-intro">
      <p>Another cluster that logs in with SSH.</p>
      {presets.length > 0 && (
        <div className="buttons" role="group" aria-label="Sigma2 clusters">
          {presets.map((preset) => (
            <button
              key={preset.name}
              onClick={() => {
                setName(preset.name)
                setHost(preset.host)
                setPort(22)
              }}
            >
              {preset.name}
            </button>
          ))}
        </div>
      )}
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
          Add cluster
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
  onInspect,
  onCwd,
}: {
  /** The tab shown, or null when the panel is hidden. */
  tab: SideTab | null
  onTab: (tab: SideTab | null) => void
  remote: RemoteStatus | null
  onRemoteChanged: () => Promise<void> | void
  /** "Import from here" on a server's tab (D122e). */
  onImportFrom: (server: RemoteServer) => void
  /** D123c: `inspect FILE` typed in a server's terminal. */
  onInspect: (target: { server: string; path: string }) => void
  /** A server's terminal reported its directory. */
  onCwd: (server: string, path: string | null) => void
}) {
  const [width, setWidth] = useState(560)
  const [picked, setPicked] = useState<string | null>(null)
  const resizing = useRef<{ x: number; width: number } | null>(null)
  const servers = remote?.enabled ? remote.servers : []
  const connected = servers.filter((s) => s.connected)
  const loggedOut = servers.filter((s) => !s.connected)
  // A server's tab whose connection ended (log out, lost) shows the HPC tab on that server, so
  // its login and the reason are there; a server removed from the list shows Claude instead.
  const asked = requested !== null && requested !== 'claude' && requested !== HPC ? requested : null
  const askedServer = asked !== null ? servers.find((s) => s.id === asked) : undefined
  let tab = requested
  let chosen = picked
  if (asked !== null && remote !== null) {
    if (askedServer === undefined) tab = 'claude'
    else if (!askedServer.connected) {
      tab = HPC
      chosen = askedServer.id
    }
  }
  if (chosen !== NEW && !loggedOut.some((s) => s.id === chosen)) chosen = loggedOut[0]?.id ?? NEW
  const current = servers.find((s) => s.id === tab)
  const label = tab === 'claude' ? 'Claude' : tab === HPC ? 'HPC' : (current?.name ?? 'Side panel')
  const open = tab !== null

  // The cluster shown in the HPC tab has logged in: show it in its own tab.
  const shown = useRef<string | null>(null)
  useEffect(() => {
    const before = servers.find((s) => s.id === shown.current)
    shown.current = tab === HPC ? chosen : null
    if (tab === HPC && before?.connected) {
      setPicked(null)
      onTab(before.id)
    }
  }, [tab, chosen, servers]) // eslint-disable-line react-hooks/exhaustive-deps

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
          {connected.map((server) => (
            <button
              key={server.id}
              role="tab"
              aria-selected={tab === server.id}
              onClick={() => onTab(server.id)}
              title={`Logged in as ${server.username}`}
            >
              {server.name}
              <span className="dot" aria-label="logged in" />
            </button>
          ))}
          {remote?.enabled && (
            <button
              role="tab"
              aria-selected={tab === HPC}
              title={connected.length ? 'Log in to another cluster' : 'Log in to a cluster such as Saga'}
              onClick={() => onTab(HPC)}
            >
              {connected.length ? '+' : 'HPC'}
            </button>
          )}
          <span className="spacer" />
          <button className="link" onClick={() => onTab(null)} title="Hide the panel; everything in it keeps running">
            Hide
          </button>
        </div>
        <ClaudePanel open={tab === 'claude'} onAttention={() => onTab('claude')} />
        {tab === HPC && remote?.enabled && (
          <div className="hpc-choice">
            <label className="field">
              <span>Cluster</span>
              <select
                value={chosen ?? NEW}
                onChange={(event) => {
                  setPicked(event.target.value)
                  onTab(HPC)
                }}
                aria-label="Cluster"
              >
                {loggedOut.map((server) => (
                  <option key={server.id} value={server.id}>
                    {server.name}
                  </option>
                ))}
                <option value={NEW}>Add a cluster…</option>
              </select>
            </label>
          </div>
        )}
        {servers.map((server) => (
          <ServerPanel
            key={server.id}
            server={server}
            servers={servers}
            open={server.connected ? tab === server.id : tab === HPC && chosen === server.id}
            onChanged={onRemoteChanged}
            onImportHere={() => onImportFrom(server)}
            onInspect={(path) => onInspect({ server: server.id, path })}
            onCwd={(path) => onCwd(server.id, path)}
          />
        ))}
        {tab === HPC && chosen === NEW && (
          <section className="side-pane">
            <NewServer servers={servers} onSaved={async (id) => {
              await onRemoteChanged()
              setPicked(id)
            }} />
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
