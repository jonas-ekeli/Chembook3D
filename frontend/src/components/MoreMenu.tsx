import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Icon, type IconName } from '../icons'

export type MenuItem = {
  label: string
  onSelect: () => void
  icon?: IconName
  /** A smaller second line, e.g. what the action has done so far. */
  detail?: ReactNode
  danger?: boolean
  disabled?: boolean
  title?: string
}

/** D179: a "⋯" button with the rare actions of a panel. Enter, Space or ↓ opens it on the first
 * item, ↑ and ↓ move, Escape closes it back onto the button; Tab or a click outside closes it. */
export function MoreMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  const entries = () => [...(wrap.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [])]

  useEffect(() => {
    if (!open) return
    entries()[0]?.focus()
    const outside = (event: PointerEvent) => {
      if (!wrap.current?.contains(event.target as globalThis.Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])

  const close = () => {
    setOpen(false)
    button.current?.focus()
  }

  const onMenuKey = (event: KeyboardEvent) => {
    const list = entries()
    const at = list.indexOf(document.activeElement as HTMLButtonElement)
    const go = (index: number) => {
      event.preventDefault()
      list[(index + list.length) % list.length]?.focus()
    }
    if (event.key === 'ArrowDown') go(at + 1)
    else if (event.key === 'ArrowUp') go(at - 1)
    else if (event.key === 'Home') go(0)
    else if (event.key === 'End') go(list.length - 1)
    else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <div className="menu more-menu" ref={wrap}>
      <button
        ref={button}
        className="icon"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' && !open) {
            event.preventDefault()
            setOpen(true)
          }
        }}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div role="menu" aria-label={label} className="menu-list" onKeyDown={onMenuKey}>
          {items.map((item) => (
            <button
              key={item.label}
              role="menuitem"
              className={item.danger ? 'danger' : undefined}
              aria-label={item.label}
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false)
                item.onSelect()
              }}
            >
              {item.icon && <Icon name={item.icon} />}
              <span className="menu-item-text">
                {item.label}
                {item.detail && <small className="muted">{item.detail}</small>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
