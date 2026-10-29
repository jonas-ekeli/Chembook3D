import { useEffect } from 'react'
import type { IconName } from './icons'

// D184: the right-click menus, the command palette (Ctrl K) and the canvas's single-key
// shortcuts are new ways to the actions the panels and bars already have, never new behaviour.

/** An action asked of a panel that holds it (the node, group, edge or selection inspector, the
 * energy drawer, the canvas, the Analyses view), so a menu or the palette runs the panel's own
 * code: its confirmation dialogs, its checks. `target` is the record's id, or the panel's name. */
export type Request = { target: string; action: string; n: number }

let asked = 0
export function request(target: string, action: string): Request {
  asked += 1
  return { target, action, n: asked }
}

// Each request runs once, even when the panel that took it is shown again later.
const handled = new Set<number>()

/** Runs `run(action)` once for each new request addressed to `target`, also when the panel is
 * only shown by the same change that asked it (a right-click selects, then asks). */
export function useRequest(asking: Request | null | undefined, target: string | null, run: (action: string) => void) {
  useEffect(() => {
    if (!asking || target === null || asking.target !== target || handled.has(asking.n)) return
    handled.add(asking.n)
    run(asking.action)
  }, [asking, target, run])
}

/** Apple keyboards say ⌘ where others say Ctrl; the palette opens with that key and K. */
export const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
export const MOD = MAC ? '⌘' : 'Ctrl'
export const PALETTE_KEY = `${MOD} K`

/** Whether a key press is the palette's: Ctrl K, or ⌘ K on a Mac (where Ctrl K is a text
 * field's "delete to the end of the line"). */
export function isPaletteKey(event: KeyboardEvent): boolean {
  return (
    event.key.toLowerCase() === 'k' &&
    !event.altKey &&
    !event.shiftKey &&
    (MAC ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey)
  )
}

/** Where typed keys belong to what has focus: a field, an editable note, a terminal (Ctrl K
 * deletes to the end of the line in a shell) or Ketcher. */
export function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return true
  return Boolean(target.closest('.xterm, .ketcher-drawer'))
}

/** A dialog or window is open over the app (Modal, Ketcher): the keys are its own. */
export function dialogOpen(): boolean {
  return document.querySelector('[aria-modal="true"]') !== null
}

/** The canvas's single-key shortcuts (D184), listed by "?" and shown in the menus. */
export const SHORTCUTS: { keys: string; what: string }[] = [
  { keys: PALETTE_KEY, what: 'Search nodes, branches, steps and commands (anywhere)' },
  { keys: 'Space', what: 'Open the selected node’s 3D view' },
  { keys: 'R', what: 'Set the selected node or group as the energy reference' },
  { keys: 'I', what: 'Import an output onto the selected node' },
  { keys: `${MOD} C`, what: 'Copy the selected node’s xyz' },
  { keys: 'Del', what: 'Delete the selected node, group or transition (asks first)' },
  { keys: 'F', what: 'Fit the selection, or the whole canvas, into view' },
  { keys: 'C, E, S', what: 'Node view: Compact, Energy, Structure' },
  { keys: 'P', what: 'Show or hide the profile and table' },
  { keys: '?', what: 'This list' },
]

/** One entry of the command palette. */
export type PaletteEntry = {
  id: string
  group: PaletteGroup
  label: string
  icon: IconName
  /** Muted text after the label: a node's step, branch and ΔX. */
  detail?: string
  /** Searched as well as the label, never shown. */
  keywords?: string
  shortcut?: string
  /** A node that is a transition state carries the canvas's TS mark, searched as "TS" before
   * its label. */
  ts?: boolean
  /** A branch's colour. */
  colour?: string
  run: () => void
}
export type PaletteGroup = 'Commands' | 'Nodes' | 'Sub-investigations' | 'Branches' | 'Steps'

/** Where a query's letters are found in a text, and how well: each word typed as one run (best
 * at the start of a word), else its letters at the starts of the text's words, a word's next
 * letters following on ("ts" in "transition states", "trst" in "TRansition STates"). Null: not
 * found. */
export function fuzzy(query: string, text: string): { score: number; marks: number[] } | null {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return { score: 0, marks: [] }
  const lower = text.toLowerCase()
  const starts = new Set<number>()
  for (let i = 0; i < lower.length; i++) if (i === 0 || /[^\p{L}\p{N}]/u.test(lower[i - 1])) starts.add(i)
  let score = 0
  const marks = new Set<number>()
  for (const word of words) {
    const found = run(lower, word, starts) ?? initials(lower, word, starts)
    if (!found) return null
    score += found.score
    for (const m of found.marks) marks.add(m)
  }
  return { score, marks: [...marks].sort((a, b) => a - b) }
}

type Found = { score: number; marks: number[] }
const span = (from: number, length: number) => Array.from({ length }, (_, i) => from + i)

function run(text: string, word: string, starts: Set<number>): Found | null {
  let best: number | null = null
  for (let at = text.indexOf(word); at >= 0; at = text.indexOf(word, at + 1)) {
    if (starts.has(at)) {
      best = at
      break
    }
    best ??= at
  }
  if (best === null) return null
  const score = (starts.has(best) ? 100 : 60) - Math.min(best, 30) / 3 + (best === 0 ? 10 : 0)
  return { score, marks: span(best, word.length) }
}

function initials(text: string, word: string, starts: Set<number>): Found | null {
  const marks: number[] = []
  let at = 0
  for (const letter of word) {
    // The next letter of the same word, else the start of a later word.
    const last = marks[marks.length - 1]
    if (last !== undefined && text[last + 1] === letter) {
      marks.push(last + 1)
      at = last + 2
      continue
    }
    let found = -1
    for (let i = at; i < text.length; i++)
      if (starts.has(i) && text[i] === letter) {
        found = i
        break
      }
    if (found < 0) return null
    marks.push(found)
    at = found + 1
  }
  return { score: 40, marks }
}
