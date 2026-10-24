import { useEffect, type ReactNode } from 'react'

export function Modal({
  title,
  onClose,
  children,
  actions,
  wide = false,
  className,
  head,
  note,
}: {
  title: string
  onClose: () => void
  children: ReactNode
  actions: ReactNode
  wide?: boolean
  /** A class of its own, for a dialog laid out apart (Settings, D183). */
  className?: string
  /** Shown beside the title, at the right (Settings' search). */
  head?: ReactNode
  /** A line at the left of the buttons. */
  note?: ReactNode
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className={['modal', wide && 'wide', className].filter(Boolean).join(' ')}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {head ? (
          <div className="modal-head">
            <h2>{title}</h2>
            {head}
          </div>
        ) : (
          <h2>{title}</h2>
        )}
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          {note && <span className="modal-note">{note}</span>}
          {actions}
        </div>
      </div>
    </div>
  )
}
