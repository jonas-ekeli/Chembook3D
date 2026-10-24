import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Icon, type IconName } from '../icons'
import { menuEntries, moveInMenu } from '../menuKeys'

export type MenuItem = {
  label: string
  onSelect: () => void
  icon?: IconName
  /** A smaller second line, e.g. what the action has done so far. */
  detail?: ReactNode
  danger?: boolean
  disabled?: boolean
  title?: string
  /** D180: a line above the item, setting it apart from the ones before. */
  separated?: boolean
  /** D184: the key that does the same, shown at the right ("R", "Ctrl C"). */
  shortcut?: string
  /** D184: a list of choices opened from this item (a right-click menu's "Status ›"); its
   * `onSelect` is then not used. */
  items?: MenuItem[]
  /** D184: in such a list, the choice in force (a status). */
  checked?: boolean
  /** D184: a short value at the right, such as a submenu's current choice. */
  aside?: string
}

/** D179: a "⋯" button with the rare actions of a panel. */
export function MoreMenu({ label, items }: { label: string; items: MenuItem[] }) {
  return (
    <Menu label={label} items={items} className="more-menu" buttonClass="icon" buttonLabel={label} title={label}>
      <Icon name="more" />
    </Menu>
  )
}

/** A button that opens a list of actions (D179; D180: also the investigation menu and the
 * canvas's Export). Enter, Space or ↓ opens it on the first item, ↑ and ↓ move, Escape closes it
 * back onto the button; Tab or a click outside closes it. */
export function Menu({
  label,
  items,
  children,
  className,
  buttonClass,
  buttonLabel,
  title,
  heading,
}: {
  /** The list's name. */
  label: string
  items: MenuItem[]
  /** What the button shows. */
  children: ReactNode
  className?: string
  buttonClass?: string
  /** The button's accessible name, when its text is not enough. */
  buttonLabel?: string
  title?: string
  /** A small heading at the top of the list (D180: the investigation's name). */
  heading?: string
}) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)

  const list = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    menuEntries(list.current)[0]?.focus()
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
    if (moveInMenu(event, list.current)) return
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === 'Tab') setOpen(false)
  }

  return (
    <div className={className ? `menu ${className}` : 'menu'} ref={wrap}>
      <button
        ref={button}
        className={buttonClass}
        aria-label={buttonLabel}
        title={title}
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
        {children}
      </button>
      {open && (
        <div ref={list} role="menu" aria-label={label} className="menu-list" onKeyDown={onMenuKey}>
          {heading && (
            <div className="menu-heading" aria-hidden="true">
              {heading}
            </div>
          )}
          {items.map((item) => (
            <MenuEntry key={item.label} item={item} onChosen={() => setOpen(false)} />
          ))}
        </div>
      )}
    </div>
  )
}

/** One entry of a menu list. D184: with `items` it opens a list of its own (`onOpen`), and a
 * choice among such items (`checked` set) is a radio entry. */
export function MenuEntry({
  item,
  onChosen,
  onOpen,
  open,
  onPointerEnter,
  index,
}: {
  item: MenuItem
  onChosen: () => void
  onOpen?: () => void
  open?: boolean
  onPointerEnter?: () => void
  /** Its place in the list, for a menu that finds its entries again. */
  index?: number
}) {
  const parent = item.items !== undefined
  return (
    <>
      {item.separated && <div role="separator" className="menu-separator" />}
      <button
        role={item.checked !== undefined ? 'menuitemradio' : 'menuitem'}
        aria-checked={item.checked}
        aria-haspopup={parent ? 'menu' : undefined}
        aria-expanded={parent ? Boolean(open) : undefined}
        className={item.danger ? 'danger' : undefined}
        aria-label={item.label}
        disabled={item.disabled}
        title={item.title}
        data-index={index}
        onPointerEnter={onPointerEnter}
        onClick={() => {
          if (parent) {
            onOpen?.()
            return
          }
          onChosen()
          item.onSelect()
        }}
      >
        {item.icon ? (
          <Icon name={item.icon} />
        ) : (
          item.checked !== undefined && <span className="menu-check">{item.checked ? '✓' : ''}</span>
        )}
        <span className="menu-item-text">
          {item.label}
          {item.detail && <small className="muted">{item.detail}</small>}
        </span>
        {item.aside && <span className="menu-aside">{item.aside}</span>}
        {item.shortcut && <kbd className="menu-key">{item.shortcut}</kbd>}
        {parent && <Icon name="chevronright" />}
      </button>
    </>
  )
}
