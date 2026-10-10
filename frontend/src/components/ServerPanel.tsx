import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type LoginStep, type RemoteServer, type ServerFields } from '../api'
import { browserKey, openXterm, socketUrl, type Xterm } from '../terminal'

// D122: a server's tab in the side panel (SidePanel.tsx). Logged out, it shows the server's
// details and "Log in", which shows the server's own prompts (password, then the one-time
// code) and an unknown host key to trust. Logged in, it is a terminal on the shell the keeper
// holds, so closing the app and starting it again shows the same shell.

type Shell = Xterm & {
  socket: WebSocket | null
  /** The shell or the connection ended, or the pane closed it; a closing socket is expected. */
  ended: boolean
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === 'string') return err.detail
  return err instanceof Error ? err.message : String(err)
}

function fields(server: RemoteServer): ServerFields {
  return { id: server.id, name: server.name, host: server.host, port: server.port, username: server.username }
}

function since(seconds: number | null): string {
  if (seconds === null) return ''
  return new Date(seconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export function ServerPanel({
  server,
  servers,
  open,
  onChanged,
}: {
  server: RemoteServer
  /** All saved servers, to save this one's changes among them. */
  servers: RemoteServer[]
  /** The panel is shown on this tab. */
  open: boolean
  /** Reload the servers and their connections. */
  onChanged: () => Promise<void> | void
}) {
  const [edit, setEdit] = useState<ServerFields>(fields(server))
  const [error, setError] = useState<string | null>(null)
  const [step, setStep] = useState<LoginStep | null>(null)
  const [answers, setAnswers] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [cwd, setCwd] = useState<string | null>(server.cwd)
  const [ended, setEnded] = useState<string | null>(null)
  const host = useRef<HTMLDivElement>(null)
  const shell = useRef<Shell | null>(null)
  const firstInput = useRef<HTMLInputElement>(null)

  // Follow the saved details and the reported directory when they change (React's pattern for
  // state reset from props, done while rendering).
  const saved = JSON.stringify(fields(server))
  const [seen, setSeen] = useState({ saved, cwd: server.cwd })
  if (seen.saved !== saved || seen.cwd !== server.cwd) {
    setSeen({ saved, cwd: server.cwd })
    if (seen.saved !== saved) setEdit(fields(server))
    if (seen.cwd !== server.cwd) setCwd(server.cwd)
  }

  const changed =
    edit.name !== server.name ||
    edit.host !== server.host ||
    edit.port !== server.port ||
    edit.username !== server.username

  const detach = () => {
    const current = shell.current
    shell.current = null
    if (current) {
      current.ended = true
      current.socket?.close()
      current.dispose()
    }
  }
  useEffect(() => detach, [])

  // Attach the terminal to the keeper's shell once the tab is shown while logged in.
  const attach = async () => {
    detach()
    setEnded(null)
    const element = host.current
    if (!element) return
    const xterm = await openXterm(element)
    const { term } = xterm
    const current: Shell = { ...xterm, socket: null, ended: false }
    shell.current = current
    try {
      const { token } = await api.remoteTerminal(server.id)
      if (shell.current !== current) return
      const socket = new WebSocket(
        socketUrl('/api/remote/terminal', { token, rows: String(term.rows), cols: String(term.cols) }),
      )
      current.socket = socket
      const send = (message: object) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
      }
      socket.onopen = () => term.focus()
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as
          | { type: 'output'; data: string }
          | { type: 'hello'; cwd: string | null }
          | { type: 'cwd'; path: string }
          | { type: 'exit'; message: string }
        if (message.type === 'output') term.write(message.data)
        else if (message.type === 'hello') setCwd(message.cwd)
        else if (message.type === 'cwd') setCwd(message.path)
        else {
          current.ended = true
          if (shell.current === current) setEnded(message.message)
          void onChanged()
        }
      }
      socket.onclose = (event) => {
        if (current.ended || shell.current !== current) return
        setEnded(
          event.code === 1008
            ? 'The app refused the connection to the terminal.'
            : 'The terminal lost its connection to the app.',
        )
      }
      term.onData((data) => send({ type: 'input', data }))
      term.onResize(({ rows, cols }) => send({ type: 'resize', rows, cols }))
      term.attachCustomKeyEventHandler((event) => !browserKey(event, term))
    } catch (err) {
      setEnded(errorMessage(err))
    }
  }

  // Attach when the tab is shown while logged in; let go when the connection has ended. A
  // shell that ended keeps its output on screen until a new one starts.
  useEffect(() => {
    if (!server.connected) {
      if (shell.current) detach()
      return
    }
    if (open && server.shell === 'running' && shell.current === null) void attach()
  }, [open, server.connected, server.shell]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const current = shell.current
    if (!open || !current) return
    current.fit.fit()
    current.term.focus()
  }, [open])

  useEffect(() => {
    if (step?.kind === 'prompts') firstInput.current?.focus()
  }, [step])

  const size = () => {
    const term = shell.current?.term
    return { rows: term?.rows ?? 30, cols: term?.cols ?? 100 }
  }

  const follow = async (next: Promise<LoginStep>) => {
    setBusy(true)
    setError(null)
    try {
      let result = await next
      while (result.kind === 'waiting' && result.login) result = await api.remoteNext(result.login)
      setAnswers([])
      if (result.kind === 'connected') {
        setStep(null)
        await onChanged()
      } else if (result.kind === 'failed') {
        setStep(null)
        setError(result.message)
      } else {
        setAnswers(result.kind === 'prompts' ? result.prompts.map(() => '') : [])
        setStep(result)
      }
    } catch (err) {
      setStep(null)
      setAnswers([])
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const logIn = () => {
    const { rows, cols } = size()
    void follow(api.remoteLogin(server.id, { username: edit.username.trim(), rows, cols }).then(async (first) => {
      await onChanged() // the user name is saved with the server
      return first
    }))
  }

  const cancel = async () => {
    const login = step?.login
    setStep(null)
    setAnswers([])
    if (login) await api.remoteCancel(login).catch(() => undefined)
  }

  const save = async () => {
    setError(null)
    try {
      await api.saveServers(servers.map((s) => (s.id === server.id ? edit : fields(s))))
      await onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const remove = async () => {
    if (!window.confirm(`Remove ${server.name} from the list? Nothing on the server changes.`)) return
    try {
      await api.saveServers(servers.filter((s) => s.id !== server.id).map(fields))
      await onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const logOut = async () => {
    detach()
    await api.remoteLogout(server.id).catch(() => undefined)
    setEnded(null)
    await onChanged()
  }

  const newShell = async () => {
    const { rows, cols } = size()
    try {
      await api.remoteShell(server.id, rows, cols)
      await onChanged()
      await attach()
    } catch (err) {
      setEnded(errorMessage(err))
    }
  }

  const account = `${server.username}@${server.host}${server.port === 22 ? '' : `:${server.port}`}`
  const login = step !== null && (
    <div className="server-login" role="group" aria-label={`Log in to ${server.name}`}>
      {step.kind === 'host_key' ? (
        <>
          <p>
            {server.name}'s host key is not known on this computer yet. Compare its fingerprint with the one the
            server's administrators publish before you trust it.
          </p>
          <p>
            <code className="fingerprint">{step.fingerprint}</code> <span className="muted small">({step.algorithm})</span>
          </p>
          <div className="buttons">
            <button className="primary" disabled={busy} onClick={() => void follow(api.remoteTrust(step.login!))}>
              Trust and continue
            </button>
            <button onClick={() => void cancel()}>Cancel</button>
          </div>
        </>
      ) : (
        <form
          autoComplete="off"
          onSubmit={(event) => {
            event.preventDefault()
            void follow(api.remoteAnswer(step.login!, answers))
          }}
        >
          {(step.banner || step.instructions || step.name) && (
            <pre className="server-banner">{[step.banner, step.name, step.instructions].filter(Boolean).join('\n')}</pre>
          )}
          {step.prompts.map((prompt, index) => (
            <label key={index} className="field">
              <span>{prompt.text.trim() || 'Answer'}</span>
              <input
                ref={index === 0 ? firstInput : undefined}
                type={prompt.echo ? 'text' : 'password'}
                autoComplete={/code|otp|token|verification/i.test(prompt.text) ? 'one-time-code' : 'off'}
                value={answers[index] ?? ''}
                onChange={(event) => setAnswers(answers.map((a, i) => (i === index ? event.target.value : a)))}
              />
            </label>
          ))}
          <p className="muted small">Sent once to {server.name}; never saved. The app does not try again by itself.</p>
          <div className="buttons">
            <button type="submit" className="primary" disabled={busy}>
              Send
            </button>
            <button type="button" onClick={() => void cancel()}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </div>
  )

  return (
    <section className="side-pane" hidden={!open}>
      <div className="claude-head">
        <strong>{server.name}</strong>
        {server.connected ? (
          <span className="muted small" title={`Logged in at ${since(server.since)}`}>
            {account}
          </span>
        ) : (
          <span className="muted small">Not logged in</span>
        )}
        <span className="spacer" />
        {server.connected && <button onClick={() => void logOut()}>Log out</button>}
      </div>
      {server.connected && (
        <div className="server-cwd" data-testid="server-cwd">
          {server.tracked || cwd ? (
            <>
              <span className="muted small">Current directory</span> <code>{cwd ?? '…'}</code>
            </>
          ) : (
            <span className="muted small">This shell does not report its current directory.</span>
          )}
        </div>
      )}
      {!server.connected && (
        <div className="claude-intro">
          {server.lost && (
            <p role="alert" className="error">
              {server.lost}
            </p>
          )}
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {login || (
            <>
              <p>
                Log in with your password and one-time code. The connection stays open in the background, so closing
                and starting Chembook3D again keeps you logged in; a restart of the computer or a lost network needs a
                new login.
              </p>
              <div className="server-fields">
                <label className="field">
                  <span>User name</span>
                  <input
                    value={edit.username}
                    autoComplete="username"
                    onChange={(event) => setEdit({ ...edit, username: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Host</span>
                  <input value={edit.host} onChange={(event) => setEdit({ ...edit, host: event.target.value })} />
                </label>
                <label className="field">
                  <span>Port</span>
                  <input
                    type="number"
                    min={1}
                    max={65535}
                    value={edit.port}
                    onChange={(event) => setEdit({ ...edit, port: Number(event.target.value) })}
                  />
                </label>
                <label className="field">
                  <span>Name</span>
                  <input value={edit.name} onChange={(event) => setEdit({ ...edit, name: event.target.value })} />
                </label>
              </div>
              <div className="buttons">
                <button className="primary" disabled={busy || !edit.username.trim()} onClick={logIn}>
                  Log in
                </button>
                {changed && (
                  <button onClick={() => void save()} disabled={busy}>
                    Save
                  </button>
                )}
                <span className="spacer" />
                <button className="link" onClick={() => void remove()}>
                  Remove from the list
                </button>
              </div>
            </>
          )}
        </div>
      )}
      <div
        className="claude-terminal"
        ref={host}
        hidden={!server.connected}
        data-testid="server-terminal"
      />
      {server.connected && ended && (
        <div className="claude-ended" role="status">
          <span>{ended}</span>
          <div className="buttons">
            {server.shell === 'ended' ? (
              <button className="primary" onClick={() => void newShell()}>
                New shell
              </button>
            ) : (
              <button onClick={() => void attach()}>Reconnect</button>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
