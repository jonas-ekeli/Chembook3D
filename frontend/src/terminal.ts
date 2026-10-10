import type { FitAddon } from '@xterm/addon-fit'
import type { Terminal } from '@xterm/xterm'

// The xterm.js terminals of the side panel: Claude Code (D92) and a server's shell (D122).

export type Xterm = {
  term: Terminal
  fit: FitAddon
  observer: ResizeObserver
  dispose: () => void
}

/** Letters typed with Ctrl that the browser, not the terminal, should handle. */
export function browserKey(event: KeyboardEvent, term: Terminal): boolean {
  const ctrl = event.ctrlKey || event.metaKey
  if (!ctrl || event.altKey) return false
  const key = event.key.toLowerCase()
  if (key === 'v') return true // paste (text through the paste event)
  if (key === 'c' && (event.shiftKey || term.hasSelection())) {
    if (event.type === 'keydown') {
      void navigator.clipboard?.writeText(term.getSelection())
      term.clearSelection()
    }
    return true
  }
  return false
}

/** A terminal in `element`, fitted to it and kept fitted. */
export async function openXterm(element: HTMLElement): Promise<Xterm> {
  const [{ Terminal }, { FitAddon }, { WebLinksAddon }] = await Promise.all([
    import('@xterm/xterm'),
    import('@xterm/addon-fit'),
    import('@xterm/addon-web-links'),
    import('@xterm/xterm/css/xterm.css'),
  ])
  const term = new Terminal({
    fontFamily: "ui-monospace, 'Cascadia Mono', Consolas, Menlo, 'DejaVu Sans Mono', monospace",
    fontSize: 13,
    cursorBlink: true,
    scrollback: 5000,
    theme: { background: '#1e1e1e' },
  })
  const fit = new FitAddon()
  term.loadAddon(fit)
  // Links printed in the terminal (Claude Code's sign-in page among them) open in a new tab.
  term.loadAddon(new WebLinksAddon())
  term.open(element)
  fit.fit()
  const observer = new ResizeObserver(() => {
    if (element.offsetWidth > 0 && element.offsetHeight > 0) fit.fit()
  })
  observer.observe(element)
  return {
    term,
    fit,
    observer,
    dispose: () => {
      observer.disconnect()
      term.dispose()
    },
  }
}

/** The WebSocket address of an app route, on the page's own host. */
export function socketUrl(path: string, query: Record<string, string>): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}${path}?${new URLSearchParams(query)}`
}
