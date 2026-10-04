import { useEffect, useState } from 'react'
import { api, type ClaudeRequest } from '../api'
import { Modal } from './Modal'

const TITLES: Record<string, string> = {
  dissolve_group: 'Claude asks to dissolve a group',
  remove_coordinates: 'Claude asks to remove coordinates',
}

/** D91: Claude cannot delete anything itself; it asks, and this dialog is where the user
 * decides. Closing the dialog refuses. */
export function ClaudeRequestDialog({
  request,
  onError,
}: {
  request: ClaudeRequest
  onError: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [left, setLeft] = useState(Math.round(request.seconds_left))
  useEffect(() => {
    const timer = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000)
    return () => clearInterval(timer)
  }, [])

  const answer = (confirm: boolean) => {
    if (busy) return
    setBusy(true)
    api.answerClaude(request.id, confirm).then(
      (answered) => {
        setBusy(false)
        if (answered.status === 'failed') onError(`Claude's request could not be done: ${answered.error}`)
      },
      (err: unknown) => {
        setBusy(false)
        onError(err instanceof Error ? err.message : String(err))
      },
    )
  }

  return (
    <Modal
      title={TITLES[request.action] ?? 'Claude asks to delete'}
      onClose={() => answer(false)}
      actions={
        <>
          <button disabled={busy} onClick={() => answer(false)}>
            Refuse
          </button>
          <button className="danger" disabled={busy} onClick={() => answer(true)}>
            Confirm
          </button>
        </>
      }
    >
      <p>{request.summary}.</p>
      {request.reason && <p>Claude's reason: {request.reason}</p>}
      <p className="muted">
        Nothing changes unless you confirm. Without an answer this is refused in {left} s.
      </p>
    </Modal>
  )
}
