// D185: the app's colours, light or dark. The choice is an app setting (`settings.theme`) and is
// mirrored in localStorage, so index.html's first script sets `data-theme` on <html> before the
// page is drawn. "system" follows prefers-color-scheme and changes with it. The read-only copy
// never sets `data-theme`, so it stays light; so do exported figures, which carry their own colours.

import { useSyncExternalStore } from 'react'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type Theme = 'light' | 'dark'

/** Read by the script in index.html; keep the two in step. */
export const THEME_KEY = 'chembook3d.theme'

const DARK_QUERY = '(prefers-color-scheme: dark)'
let choice: ThemeChoice = 'system'
let active = false // only the app sets a theme; the read-only copy never does
const listeners = new Set<() => void>()

function systemDark(): boolean {
  return typeof matchMedia === 'function' && matchMedia(DARK_QUERY).matches
}

function resolved(): Theme {
  return choice === 'dark' || (choice === 'system' && systemDark()) ? 'dark' : 'light'
}

function apply() {
  const theme = resolved()
  if (document.documentElement.dataset.theme !== theme) {
    document.documentElement.dataset.theme = theme
    listeners.forEach((listener) => listener())
  }
}

if (typeof matchMedia === 'function') {
  matchMedia(DARK_QUERY).addEventListener('change', () => {
    if (active && choice === 'system') apply()
  })
}

/** Use `next` from now on (the settings' value once they load or change). */
export function setTheme(next: ThemeChoice) {
  active = true
  choice = next
  try {
    if (next === 'system') localStorage.removeItem(THEME_KEY)
    else localStorage.setItem(THEME_KEY, next)
  } catch {
    // Without storage the page still follows the setting once it loads.
  }
  apply()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function current(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

/** The theme shown now, for what paints outside CSS (the 3D views, the terminals, React Flow). */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, () => 'light')
}
