import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type ClaudeAutonomy, type ClaudeStatus, type LaunchQuestion as Question } from '../api'
import { browserKey, openXterm, socketUrl, type Xterm } from '../terminal'
import { LaunchQuestion } from './LaunchQuestion'
import { Modal } from './Modal'
import { errorText, limitText } from '../util'

// D92: the Claude tab of the side panel (SidePanel.tsx), a terminal running the official
// Claude Code CLI on this computer (src/chembook3d/claude_panel.py). It stays mounted while
// hidden, so hiding it keeps the conversation running; the app remounts it for another
// investigation, which stops Claude Code.

type Phase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'running' }
  | { kind: 'ended'; message: string }

type Session = Xterm & {
  socket: WebSocket | null
  /** Claude Code ended or was stopped; a closing connection is then expected. */
  ended: boolean
}

/** D223: the warning shown before Claude may work on its own, with what it may start. */
function AutonomyDialog({
  autonomy,
  onCancel,
  onTurnOn,
}: {
  autonomy: ClaudeAutonomy
  onCancel: () => void
  onTurnOn: (jobLimit: number, cloud: boolean) => void
}) {
  const [limit, setLimit] = useState(autonomy.job_limit)
  const [cloud, setCloud] = useState(false)
  return (
    <Modal
      title="Let Claude work on its own?"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="danger" onClick={() => onTurnOn(limit, cloud)}>
            Turn on
          </button>
        </>
      }
    >
      <p>
        While this is on, Claude changes this investigation without asking you first. It creates and changes nodes,
        edges, steps, branches, groups and analyses, imports files, and starts calculations on this computer. It also
        makes choices the app otherwise leaves to you, such as which pathway to follow or which conformer represents a
        group, and notes each one as “Claude chose”. It also opens web pages without asking, and a page can mislead
        it.
      </p>
      <p>
        <strong>Claude can be wrong.</strong> Check its work before you rely on it: every change is marked “claude” in
        the history, and a copy of the investigation is saved now, in its backups folder.
      </p>
      <p className="muted">
        Still asked or switched off: deleting anything Claude did not make itself, everything on a server beyond new
        files where you allowed them, and shell and file tools. It turns off when the app restarts.
      </p>
      <label>
        Longest local job Claude may start{' '}
        <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
          {autonomy.job_limits.map((seconds) => (
            <option key={seconds} value={seconds}>
              {limitText(seconds)}
            </option>
          ))}
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={cloud} onChange={(event) => setCloud(event.target.checked)} /> Also start
        cloud sessions (uses your Claude usage)
      </label>
    </Modal>
  )
}

/** How often the panel looks for a cloud job's launch that waits for an answer (ms). */
const QUESTION_POLL = 2500

export function ClaudePanel({
  open,
  onAttention,
}: {
  /** The panel is shown on this tab. */
  open: boolean
  /** A cloud job's launch asks something: show the panel. */
  onAttention: () => void
}) {
  const [status, setStatus] = useState<ClaudeStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const host = useRef<HTMLDivElement>(null)
  const session = useRef<Session | null>(null)
  const [question, setQuestion] = useState<Question | null>(null)
  const [autonomy, setAutonomy] = useState<ClaudeAutonomy | null>(null)
  const [asking, setAsking] = useState(false)
  const attention = useRef(onAttention)
  useEffect(() => {
    attention.current = onAttention
  }, [onAttention])

  // D93: a `claude --cloud` launch that stops at a question is answered here, never in
  // another terminal.
  useEffect(() => {
    if (question !== null || !status?.enabled) return
    let stopped = false
    const look = () =>
      api.claudeLaunches().then(
        (waiting) => {
          if (stopped || waiting.length === 0) return
          setQuestion(waiting[0])
          attention.current()
        },
        () => undefined,
      )
    void look()
    const timer = window.setInterval(() => void look(), QUESTION_POLL)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [question, status?.enabled])

  const load = () =>
    api.claudeStatus().then(
      (next) => {
        setStatusError(null)
        setStatus(next)
      },
      (err: unknown) => setStatusError(errorText(err)),
    )

  useEffect(() => {
    if (status === null) void load()
  }, [status])

  // D223: whether Claude works on its own here; read again when the panel is shown, since
  // another tab may have changed it.
  const usable = status?.enabled === true && status.available && status.notebook_tools
  useEffect(() => {
    if (!usable || !open) return
    let stopped = false
    api.claudeAutonomy().then(
      (next) => {
        if (!stopped) setAutonomy(next)
      },
      () => undefined,
    )
    return () => {
      stopped = true
    }
  }, [usable, open])

  // Stop Claude Code when the panel goes away (another investigation, or closing it).
  useEffect(
    () => () => {
      const current = session.current
      session.current = null
      current?.socket?.close()
      current?.dispose()
    },
    [],
  )

  // Fit the terminal when the panel is shown again.
  useEffect(() => {
    const current = session.current
    if (!open || !current) return
    current.fit.fit()
    current.term.focus()
  }, [open])

  const start = async (resume: boolean) => {
    setPhase({ kind: 'starting' })
    const previous = session.current
    session.current = null
    previous?.socket?.close()
    previous?.dispose()
    try {
      const { token } = await api.claudeSession(resume)
      const element = host.current
      if (!element) return
      const xterm = await openXterm(element)
      const { term } = xterm
      const current: Session = { ...xterm, socket: null, ended: false }
      session.current = current

      const socket = new WebSocket(
        socketUrl('/api/claude/terminal', { token, rows: String(term.rows), cols: String(term.cols) }),
      )
      current.socket = socket
      const send = (message: object) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message))
      }
      socket.onopen = () => {
        setPhase({ kind: 'running' })
        term.focus()
      }
      socket.onmessage = (event) => {
        const message = JSON.parse(String(event.data)) as
          | { type: 'output'; data: string }
          | { type: 'exit'; code: number | null }
        if (message.type === 'output') term.write(message.data)
        else {
          current.ended = true
          if (session.current === current) setPhase({ kind: 'ended', message: 'Claude Code has ended.' })
        }
      }
      socket.onclose = (event) => {
        if (current.ended || session.current !== current) return
        setPhase({
          kind: 'ended',
          message:
            event.code === 1008
              ? 'The app refused the connection. Start Claude again from this window.'
              : event.code === 1013
                ? 'Too many Claude panels are open. Close one in another tab, then start again.'
                : 'The connection to Claude Code was lost.',
        })
      }
      term.onData((data) => send({ type: 'input', data }))
      term.onResize(({ rows, cols }) => send({ type: 'resize', rows, cols }))
      term.attachCustomKeyEventHandler((event) => {
        if (browserKey(event, term)) return false
        // Shift+Enter starts a new line in Claude Code's prompt (as its /terminal-setup does).
        if (event.key === 'Enter' && event.shiftKey && !event.ctrlKey && !event.altKey) {
          if (event.type === 'keydown') send({ type: 'input', data: '\x1b\r' })
          return false
        }
        return true
      })
    } catch (err) {
      setPhase({ kind: 'ended', message: errorText(err) })
      setStatus(null)
    }
  }

  const stop = () => {
    const current = session.current
    if (!current) return
    current.ended = true
    current.socket?.close()
    setPhase({ kind: 'ended', message: 'Claude Code was stopped.' })
  }

  // D223: the app stops every Claude terminal when the switch changes, because a session's
  // permissions are written when it starts. Turning it on starts Claude again in the same
  // conversation; turning it off leaves it stopped, which is what Stop is for.
  const switchAutonomy = async (on: boolean, jobLimit?: number, cloud?: boolean) => {
    const current = session.current
    const wasRunning = phase.kind === 'running' || phase.kind === 'starting'
    if (current) current.ended = true
    setAsking(false)
    try {
      setAutonomy(await api.setClaudeAutonomy({ on, job_limit: jobLimit, cloud }))
    } catch (err) {
      if (current) current.ended = false
      setPhase({ kind: 'ended', message: errorText(err) })
      return
    }
    if (!wasRunning) return
    current?.socket?.close()
    if (on) void start(true)
    else setPhase({ kind: 'ended', message: 'Claude was stopped. It asks before each change again.' })
  }

  // A picture on the clipboard pastes no text; Claude Code reads it from the clipboard
  // itself when it gets Ctrl+V.
  const onPaste = (event: React.ClipboardEvent) => {
    const current = session.current
    if (!current || event.clipboardData.getData('text')) return
    if (current.socket?.readyState === WebSocket.OPEN) {
      current.socket.send(JSON.stringify({ type: 'input', data: '\x16' }))
    }
  }

  const running = phase.kind === 'running' || phase.kind === 'starting'
  const startButtons = (
    <div className="buttons">
      <button className="primary" onClick={() => void start(false)}>
        New conversation
      </button>
      <button onClick={() => void start(true)} title="claude --continue: the last conversation for this investigation">
        Continue last conversation
      </button>
    </div>
  )

  return (
    <section className="side-pane" hidden={!open}>
      <div className="claude-head">
        <strong>Claude</strong>
        <span className="muted small">
          {phase.kind === 'running' ? 'Claude Code is running' : phase.kind === 'starting' ? 'Starting…' : ''}
        </span>
        <span className="spacer" />
        {usable && autonomy && (
          <label
            className="check"
            title="Claude then changes this investigation without asking first. A warning says what that means before it is turned on."
          >
            <input
              type="checkbox"
              checked={autonomy.on}
              onChange={(event) => (event.target.checked ? setAsking(true) : void switchAutonomy(false))}
            />{' '}
            Work on its own
          </label>
        )}
        {running && <button onClick={stop}>Stop</button>}
      </div>
      {autonomy?.on && (
        <div className="claude-alone" role="status" aria-label="Working on its own">
          <span>
            <strong>Claude is working on its own.</strong> It changes this investigation without asking, and starts
            local jobs of up to {limitText(autonomy.job_limit)}
            {autonomy.cloud ? ' and cloud sessions' : ''}. Jobs it started keep running when you stop it; cancel them
            in Local jobs.
          </span>
          <button onClick={() => void switchAutonomy(false)}>Stop and turn off</button>
        </div>
      )}
      {asking &&
        autonomy &&
        // On the page, not in the panel, whose text is the terminal's.
        createPortal(
          <AutonomyDialog
            autonomy={autonomy}
            onCancel={() => setAsking(false)}
            onTurnOn={(jobLimit, cloud) => void switchAutonomy(true, jobLimit, cloud)}
          />,
          document.body,
        )}
      {question && <LaunchQuestion key={question.job_id} question={question} onDone={() => setQuestion(null)} />}
      {phase.kind === 'idle' && (
        <div className="claude-intro">
          {statusError ? (
            <p role="alert" className="error">
              {statusError}
            </p>
          ) : status === null ? (
            <p className="muted">Looking for Claude Code…</p>
          ) : !status.enabled ? (
            <p role="alert" className="error">
              {status.reason}
            </p>
          ) : !status.available ? (
            <>
              <p>
                Claude Code is not installed on this computer. Install it, run <code>claude</code> once in a
                terminal to sign in with your Claude account, then check again.
              </p>
              <pre className="claude-command">{status.install.command}</pre>
              <p>
                <a href={status.install.docs} target="_blank" rel="noreferrer">
                  Installation guide
                </a>
              </p>
              <div className="buttons">
                <button onClick={() => void load()}>Check again</button>
              </div>
            </>
          ) : (
            <>
              <p>
                Claude Code runs here with your own Claude sign-in. It works on the investigation open in this
                window, {autonomy?.on ? 'changes it without asking while it works on its own' : 'asks before it changes anything'},
                and cannot use a shell or edit files. It can search the web for the literature.
              </p>
              {!status.notebook_tools && (
                <p className="muted">
                  This version of Chembook3D has no notebook tools for Claude yet, so it can only answer from what you
                  tell it.
                </p>
              )}
              <p className="muted small">
                If Claude Code asks you to sign in or to trust this folder, follow its steps in the panel. Shift+Enter
                starts a new line; Ctrl+C with text selected copies it.
              </p>
              {startButtons}
            </>
          )}
        </div>
      )}
      <div
        className="claude-terminal"
        ref={host}
        hidden={phase.kind === 'idle'}
        onPasteCapture={onPaste}
        data-testid="claude-terminal"
      />
      {phase.kind === 'ended' && (
        <div className="claude-ended" role="status">
          <span>{phase.message}</span>
          {startButtons}
        </div>
      )}
    </section>
  )
}
