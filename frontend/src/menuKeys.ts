import type { KeyboardEvent } from 'react'

// The keys of the app's menu lists (D180), shared by the button menus and the right-click
// menus (D184).

/** The enabled entries of a menu list, in order. */
export function menuEntries(list: HTMLElement | null): HTMLButtonElement[] {
  return [
    ...(list?.querySelectorAll<HTMLButtonElement>(
      ':scope > [role^="menuitem"]:not(:disabled), :scope > .menu-entry > [role^="menuitem"]:not(:disabled)',
    ) ?? []),
  ]
}

/** ↑ and ↓ (round), Home and End between a list's entries; true when the key was one of them. */
export function moveInMenu(event: KeyboardEvent, list: HTMLElement | null): boolean {
  const entries = menuEntries(list)
  const at = entries.indexOf(document.activeElement as HTMLButtonElement)
  const go = (index: number) => {
    event.preventDefault()
    entries[(index + entries.length) % entries.length]?.focus()
    return true
  }
  if (event.key === 'ArrowDown') return go(at + 1)
  if (event.key === 'ArrowUp') return go(at < 0 ? -1 : at - 1)
  if (event.key === 'Home') return go(0)
  if (event.key === 'End') return go(entries.length - 1)
  return false
}
