import { useEffect, useRef, useState } from 'react'
import type { Terminal } from '@xterm/xterm'
import { api, type LaunchQuestion as Question } from '../api'

// D93, A40: a `claude --cloud` launch of a calculation job that stops at a question (whether
// Claude Code may trust the investigation folder, for one). The app answers none itself; this
// shows the launch's own screen at the top of the Claude panel, and the user answers it here
// with the keys Claude Code asks for.

type State =
  | { kind: 'connecting' }
  | { kind: 'open' }
  | { kind: 'named'; url: string }
  | { kind: 'ended'; message: string }

export function LaunchQuestion({ question, onDone }: { question: Question; onDone: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<State>({ kind: 'connecting' })

  useEffect(() => {
    let cancelled = false
    let term: Terminal | null = null
    let socket: WebSocket | null = null
    let finished = false
    const finish = (next: State) => {
      finished = true
      if (!cancelled) setState(next)
    }
    void (async () => {
      try {
        const [{ Terminal }, { FitAddon }] = await Promise.all([
          import('@xterm/xterm'),
          import('@xterm/addon-fit'),
          import('@xterm/xterm/css/xterm.css'),
        ])
        const { token } = await api.claudeLaunchView(question.job_id)
        const element = host.current
        if (cancelled || !element) return
        term = new Terminal({
          fontFamily: "ui-monospace, 'Cascadia Mono', Consolas, Menlo, 'DejaVu Sans Mono', monospace",
          fontSize: 12,
          rows: 16,
          theme: { background: '#1e1e1e' },
        })
        const fit = new FitAddon()
        term.loadAddon(fit)
        term.open(element)
        fit.fit()
        const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
        const query = new URLSearchParams({ token, rows: String(term.rows), cols: String(term.cols) })
        socket = new WebSocket(`${scheme}://${window.location.host}/api/claude/launch-view?${query}`)
        const current = socket
        const send = (message: object) => {
          if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify(message))
        }
        current.onopen = () => {
          if (!cancelled) setState({ kind: 'open' })
          term?.focus()
        }
        current.onmessage = (event) => {
          const message = JSON.parse(String(event.data)) as
            | { type: 'output'; data: string }
            | { type: 'named'; url: string }
            | { type: 'exit'; code: number | null }
          if (message.type === 'output') term?.write(message.data)
          else if (message.type === 'named') finish({ kind: 'named', url: message.url })
          else finish({ kind: 'ended', message: 'Claude Code ended without starting the cloud session.' })
        }
        current.onclose = () => {
          if (!finished) finish({ kind: 'ended', message: 'The connection to the launch was lost.' })
        }
        term.onData((data) => send({ type: 'input', data }))
      } catch (err) {
        finish({ kind: 'ended', message: err instanceof Error ? err.message : String(err) })
      }
    })()
    return () => {
      cancelled = true
      socket?.close()
      term?.dispose()
    }
  }, [question.job_id])

  return (
    <section className="launch-question" aria-label="Claude Code asks before starting the cloud job">
      <div className="launch-question-head">
        {state.kind === 'named' ? (
          <span role="status">
            The cloud session for “{question.name}” has started.{' '}
            <a href={state.url} target="_blank" rel="noreferrer">
              Open it on claude.ai
            </a>
          </span>
        ) : state.kind === 'ended' ? (
          <span role="alert">{state.message}</span>
        ) : (
          <span>
            Before it starts the cloud job “{question.name}”, Claude Code asks this. Answer it here with the arrow
            keys and Enter.
          </span>
        )}
        <span className="spacer" />
        {(state.kind === 'named' || state.kind === 'ended') && (
          <button className="link" onClick={onDone}>
            Close
          </button>
        )}
      </div>
      <div
        className="launch-question-screen"
        ref={host}
        hidden={state.kind === 'named' || state.kind === 'ended'}
        data-testid="launch-question"
      />
    </section>
  )
}
