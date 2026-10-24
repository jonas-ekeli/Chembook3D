import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { menuEntries, moveInMenu } from '../menuKeys'
import { MenuEntry, type MenuItem } from './MoreMenu'

/** What a right-click menu is about, shown in small type above its entries. */
export type MenuHeading = { title: string; detail?: string }

/**
 * D184: a right-click menu at the pointer, with the entries of the shared menu list (D179, D180).
 * ↑ and ↓ move, → or Enter opens an entry's own list ("Status ›") and ← or Escape closes it again;
 * Escape or Tab closes the menu, and so do a click or a scroll anywhere else and leaving the
 * window. It opens inside the window, flipped left or up near its edges.
 */
export function ContextMenu({
  x,
  y,
  content,
  onClose,
}: {
  x: number
  y: number
  /** The menu's name, heading and entries as things are now; null when its record is gone. */
  content: () => { label: string; heading?: MenuHeading; items: MenuItem[] } | null
  onClose: () => void
}) {
  const shown = content()
  const root = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)
  // The entry whose own list is open, by its index in `items`.
  const [open, setOpen] = useState<number | null>(null)
  // Focus goes back where it was (the canvas) when the menu closes.
  const before = useRef<Element | null>(null)
  const closeRef = useRef(onClose)
  useLayoutEffect(() => {
    closeRef.current = onClose
  })

  useLayoutEffect(() => {
    const box = list.current?.getBoundingClientRect()
    if (!box) return
    const margin = 8
    const left = x + box.width > window.innerWidth - margin ? Math.max(margin, x - box.width) : x
    const top = y + box.height > window.innerHeight - margin ? Math.max(margin, y - box.height) : y
    setPlace({ left, top })
  }, [x, y])

  useEffect(() => {
    before.current = document.activeElement
    const close = () => closeRef.current()
    const outside = (event: Event) => {
      if (!root.current?.contains(event.target as globalThis.Node)) close()
    }
    document.addEventListener('pointerdown', outside, true)
    document.addEventListener('wheel', outside, { capture: true, passive: true })
    document.addEventListener('scroll', outside, true)
    window.addEventListener('resize', close)
    window.addEventListener('blur', close)
    return () => {
      document.removeEventListener('pointerdown', outside, true)
      document.removeEventListener('wheel', outside, true)
      document.removeEventListener('scroll', outside, true)
      window.removeEventListener('resize', close)
      window.removeEventListener('blur', close)
    }
  }, [])

  // The first entry takes the focus once the menu is placed (and so can take it).
  const placed = place !== null
  useEffect(() => {
    if (placed) menuEntries(list.current)[0]?.focus({ preventScroll: true })
  }, [placed])

  // Its record deleted meanwhile (by Claude, another tab): nothing to offer.
  useEffect(() => {
    if (!shown) closeRef.current()
  }, [shown])

  const dismiss = () => {
    const element = before.current
    if (element instanceof HTMLElement && element.isConnected) element.focus({ preventScroll: true })
    onClose()
  }
  const entry = (index: number) => list.current?.querySelector<HTMLButtonElement>(`[data-index="${index}"]`)

  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const inList = (event.target as HTMLElement).parentElement?.closest('[role="menu"]')
    if (inList && inList !== list.current) {
      // An entry's own list: ← and Escape go back to its entry.
      if (event.key === 'ArrowLeft' || event.key === 'Escape') {
        event.preventDefault()
        const index = open
        setOpen(null)
        if (index !== null) entry(index)?.focus({ preventScroll: true })
        return
      }
      if (event.key === 'Tab') {
        event.preventDefault()
        dismiss()
        return
      }
      moveInMenu(event, inList as HTMLElement)
      return
    }
    if (event.key === 'Escape' || event.key === 'Tab') {
      event.preventDefault()
      event.stopPropagation()
      dismiss()
    } else if (event.key === 'ArrowRight') {
      const index = Number((event.target as HTMLElement).dataset.index)
      if (shown?.items[index]?.items) {
        event.preventDefault()
        setOpen(index)
      }
    } else if (moveInMenu(event, list.current)) {
      setOpen(null)
    }
  }

  if (!shown) return null
  const { label, heading, items } = shown
  return createPortal(
    <div
      ref={root}
      className="context-menu"
      style={{ left: place?.left ?? x, top: place?.top ?? y, visibility: place ? undefined : 'hidden' }}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div ref={list} role="menu" aria-label={label} className="menu-list" onKeyDown={onKey}>
        {heading && (
          <div className="context-heading" aria-hidden="true">
            <b>{heading.title}</b>
            {heading.detail && <span> · {heading.detail}</span>}
          </div>
        )}
        {items.map((item, index) =>
          item.items ? (
            <div key={item.label} className="menu-entry">
              <MenuEntry
                item={item}
                index={index}
                open={open === index}
                onOpen={() => setOpen(index)}
                onPointerEnter={() => setOpen(index)}
                onChosen={dismiss}
              />
              {open === index && <Submenu item={item} onChosen={dismiss} />}
            </div>
          ) : (
            <MenuEntry
              key={item.label}
              item={item}
              index={index}
              onPointerEnter={() => setOpen(null)}
              onChosen={dismiss}
            />
          ),
        )}
      </div>
    </div>,
    document.body,
  )
}

/** An entry's own list, beside it (to the left when there is no room on the right), opened on
 * the choice in force or the first. */
function Submenu({ item, onChosen }: { item: MenuItem; onChosen: () => void }) {
  const list = useRef<HTMLDivElement>(null)
  const [left, setLeft] = useState(false)
  useLayoutEffect(() => {
    const element = list.current
    if (!element) return
    if (element.getBoundingClientRect().right > window.innerWidth - 8) setLeft(true)
    const entries = menuEntries(element)
    ;(entries.find((e) => e.getAttribute('aria-checked') === 'true') ?? entries[0])?.focus({ preventScroll: true })
  }, [])
  return (
    <div ref={list} role="menu" aria-label={item.label} className={`menu-list context-submenu${left ? ' left' : ''}`}>
      {item.items?.map((choice) => (
        <MenuEntry key={choice.label} item={choice} onChosen={onChosen} />
      ))}
    </div>
  )
}
