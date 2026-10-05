import { useEffect, useRef, useState } from 'react'
import type { FitAddon } from '@xterm/addon-fit'
import type { Terminal } from '@xterm/xterm'
import { api, ApiError, type ClaudeStatus, type LaunchQuestion as Question } from '../api'
import { LaunchQuestion } from './LaunchQuestion'

// D92: the Claude panel, a terminal docked at the side of the window running the official
// Claude Code CLI on this computer (src/chembook3d/claude_panel.py). It stays mounted while
// hidden, so hiding it keeps the conversation running; the app remounts it for another
// investigation, which stops Claude Code.

type Phase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'running' }
  | { kind: 'ended'; message: string }

type Session = {
  term: Terminal
  fit: FitAddon
  socket: WebSocket | null
  observer: ResizeObserver
  /** Claude Code ended or was stopped; a closing connection is then expected. */
  ended: boolean
}

const MIN_WIDTH = 360
/** How often the panel looks for a cloud job's launch that waits for an answer (ms). */
const QUESTION_POLL = 2500
const MAX_WIDTH = 1400

function errorMessage(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === 'string') return err.detail
  return err instanceof Error ? err.message : String(err)
}

/** Letters typed with Ctrl that the browser, not the terminal, should handle. */
function browserKey(event: KeyboardEvent, term: Terminal): boolean {
  const ctrl = event.ctrlKey || event.metaKey
  if (!ctrl || event.altKey) return false
  const key = event.key.toLowerCase()
  if (key === 'v') return true // paste (text through the paste event; see onPaste)
  if (key === 'c' && (event.shiftKey || term.hasSelection())) {
    if (event.type === 'keydown') {
      void navigator.clipboard?.writeText(term.getSelection())
      term.clearSelection()
    }
    return true
  }
  return false
}

export function ClaudePanel({
  open,
  onClose,
  onAttention,
}: {
  open: boolean
  onClose: () => void
  /** A cloud job's launch asks something: show the panel. */
  onAttention: () => void
}) {
  const [status, setStatus] = useState<ClaudeStatus | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' })
  const [width, setWidth] = useState(560)
  const host = useRef<HTMLDivElement>(null)
  const session = useRef<Session | null>(null)
  const resizing = useRef<{ x: number; width: number } | null>(null)
  const [question, setQuestion] = useState<Question | null>(null)
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
      (err: unknown) => setStatusError(errorMessage(err)),
    )

  useEffect(() => {
    if (status === null) void load()
  }, [status])

  // Stop Claude Code when the panel goes away (another investigation, or closing it).
  useEffect(
    () => () => {
      const current = session.current
      session.current = null
      current?.socket?.close()
      current?.observer.disconnect()
      current?.term.dispose()
    },
    [],
  )

  // Fit the terminal when the panel is shown again or resized.
  useEffect(() => {
    const current = session.current
    if (!open || !current) return
    current.fit.fit()
    current.term.focus()
  }, [open, width])

  const start = async (resume: boolean) => {
    setPhase({ kind: 'starting' })
    const previous = session.current
    session.current = null
    previous?.socket?.close()
    previous?.observer.disconnect()
    previous?.term.dispose()
    try {
      const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
        import('@xterm/xterm'),
        import('@xterm/addon-fit'),
        import('@xterm/addon-web-links'),
        import('@xterm/xterm/css/xterm.css'),
      ])
      const { token } = await api.claudeSession(resume)
      const element = host.current
      if (!element) return
      const term = new Terminal({
        fontFamily: "ui-monospace, 'Cascadia Mono', Consolas, Menlo, 'DejaVu Sans Mono', monospace",
        fontSize: 13,
        cursorBlink: true,
        scrollback: 5000,
        theme: { background: '#1e1e1e' },
      })
      const fit = new FitAddon()
      term.loadAddon(fit)
      // Links Claude Code prints (its sign-in page among them) open in a new tab.
      term.loadAddon(new WebLinksAddon())
      term.open(element)
      fit.fit()
      const observer = new ResizeObserver(() => {
        if (element.offsetWidth > 0 && element.offsetHeight > 0) fit.fit()
      })
      observer.observe(element)
      const current: Session = { term, fit, socket: null, observer, ended: false }
      session.current = current

      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const query = new URLSearchParams({ token, rows: String(term.rows), cols: String(term.cols) })
      const socket = new WebSocket(`${scheme}://${window.location.host}/api/claude/terminal?${query}`)
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
      setPhase({ kind: 'ended', message: errorMessage(err) })
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
    <>
      <div
        className="splitter"
        hidden={!open}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize Claude panel"
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
      <aside className="claude-panel" hidden={!open} style={{ width }} aria-label="Claude">
        <div className="claude-head">
          <strong>Claude</strong>
          <span className="muted small">
            {phase.kind === 'running' ? 'Claude Code is running' : phase.kind === 'starting' ? 'Starting…' : ''}
          </span>
          <span className="spacer" />
          {running && <button onClick={stop}>Stop</button>}
          <button className="link" onClick={onClose} title="Hide the panel; Claude keeps running">
            Hide
          </button>
        </div>
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
                  window, asks before it changes anything, and cannot use a shell, edit files or browse the web.
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
      </aside>
    </>
  )
}
