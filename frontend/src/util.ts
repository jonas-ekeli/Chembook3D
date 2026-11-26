// Small helpers shared by the app and its read-only copy (D79).

import { ApiError } from './api'

// A13: a pathway may end at a node it visited earlier, closing a catalytic cycle once.
export function closesCycle(ids: string[]): boolean {
  return ids.slice(0, -1).includes(ids[ids.length - 1])
}

/** `text` as a file name, by the backend's rule (api/downloads.py): each of <>:"/\|?* and each
 * control character becomes "_", spaces around it and dots at its end go; primes, spaces and
 * Greek letters stay. */
export function fileName(text: string, fallback: string): string {
  // eslint-disable-next-line no-control-regex
  const name = text.replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_').replace(/^ +/, '').replace(/[. ]+$/, '')
  return name || fallback
}

/** A colour token from index.css's :root (UI12 step 1, D174), for what paints outside CSS: the 3D
 * view, the terminals. Exported figures keep their own colours. */
export function colourToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export function download(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
}

/** Save `blob` as the file `name`, freeing its object URL `revokeAfter` ms later. */
export function downloadBlob(blob: Blob, name: string, revokeAfter = 1000) {
  const url = URL.createObjectURL(blob)
  download(url, name)
  setTimeout(() => URL.revokeObjectURL(url), revokeAfter)
}

/** Build xyz text with a displacement vector per atom, which 3Dmol animates (FR-3D-03). */
export function withDisplacements(xyz: string, mode: number[][]): string {
  const lines = xyz.split(/\r?\n/)
  let atom = 0
  return lines
    .map((line, i) => {
      if (i < 2 || !line.trim()) return line
      const d = mode[atom++]
      return d ? `${line} ${d[0].toFixed(5)} ${d[1].toFixed(5)} ${d[2].toFixed(5)}` : line
    })
    .join('\n')
}

export function hartree(value: number | null | undefined, digits = 6): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(digits)} Eh`
}

/** "3–7, 10": numbers as runs. */
export function ranges(numbers: number[]): string {
  const sorted = [...numbers].sort((a, b) => a - b)
  const runs: string[] = []
  for (let i = 0; i < sorted.length; ) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
    runs.push(j > i ? `${sorted[i]}–${sorted[j]}` : String(sorted[i]))
    i = j + 1
  }
  return runs.join(', ')
}

/** The file browsers' name filter (D78), in the import dialog and in Import from here: plain
 * text matches names containing it; with * (any run of characters) or ? (one character) it must
 * match the whole name, as in a Linux shell. Case is ignored either way. */
export function nameFilter(text: string): (name: string) => boolean {
  const needle = text.trim().toLowerCase()
  if (!/[*?]/.test(needle)) return (name) => name.toLowerCase().includes(needle)
  const pattern = needle
    .split('')
    .map((c) => (c === '*' ? '.*' : c === '?' ? '.' : c.replace(/[.+^${}()|[\]\\]/g, '\\$&')))
    .join('')
  const glob = new RegExp(`^${pattern}$`, 's')
  return (name) => glob.test(name.toLowerCase())
}

/** The energy table's cell class (FR-EN-06): the energies are numbers; the last column holds
 * what the frequency job says against the point's role (D154). */
/** D177: the energy table's Export CSV tooltip, since the file keeps its own column order. */
export function csvTitle(type: string): string {
  return (
    `The file keeps its own column order (Label, Step, Branch, Level, E, H, G, G_qh, Thermochemistry, ` +
    `Free species, Δ${type}, Check) and has no Δ${type}‡ of step column`
  )
}

/** A caught error as the text shown to the user (D83, D86, D171). An ApiError's message is
 * already the backend's `detail` when that is text. With `blockers`, the reasons the backend
 * gave for refusing a change are shown instead, joined by "; ", when it sent some. */
export function errorText(err: unknown, options: { blockers?: boolean } = {}): string {
  if (options.blockers && err instanceof ApiError && err.blockers) return err.blockers.join('; ')
  return err instanceof Error ? err.message : String(err)
}

const SUPERSCRIPT: Record<string, string> = {
  '-': '⁻',
  '0': '⁰',
  '1': '¹',
  '2': '²',
  '3': '³',
  '4': '⁴',
  '5': '⁵',
  '6': '⁶',
  '7': '⁷',
  '8': '⁸',
  '9': '⁹',
}

/** 1234 as "1.23 × 10³"; between 0.01 and 1000 as a plain number. */
export function scientific(value: number | null): string {
  if (value === null) return 'n/a'
  if (!Number.isFinite(value)) return value > 0 ? '∞' : '−∞'
  if (value === 0) return '0'
  const size = Math.abs(value)
  if (size >= 0.01 && size < 1000) return value.toPrecision(3)
  const [mantissa, exponent] = value.toExponential(2).split('e')
  const power = String(Number(exponent))
    .split('')
    .map((c) => SUPERSCRIPT[c] ?? c)
    .join('')
  return `${mantissa} × 10${power}`
}

/** D263a: what "Build mechanism from folder…" types into the Claude panel: the playbook's
 * command (`.claude/commands/build-mechanism.md`), the folder in double quotes when it has
 * spaces, and `--no-literature` when the start dialog's tick box is off (D263h). One line,
 * without control characters; the panel presses Enter after it. */
export function buildMechanismCommand(folder: string, literature: boolean): string {
  // No control characters: each would be a key pressed in the terminal.
  const path = Array.from(folder, (c) => (c < ' ' || c === '\x7f' ? ' ' : c))
    .join('')
    .trim()
  const quoted = /\s/.test(path) ? `"${path.replace(/"/g, '')}"` : path
  return `/build-mechanism ${quoted}${literature ? '' : ' --no-literature'}`
}

/** D223: a job limit in words, as the backend's `autonomy.limit_text`. */
export function limitText(seconds: number): string {
  if (seconds < 3600) return `${Math.round(seconds / 60)} minutes`
  const hours = seconds / 3600
  if (hours === 1) return '1 hour'
  return hours >= 48 && hours % 24 === 0 ? `${hours / 24} days` : `${hours} hours`
}

/** "just now", "5 min ago", "2 h ago", "yesterday", "4 days ago", then the date. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const when = new Date(iso)
  const minutes = Math.round((now.getTime() - when.getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`
  const midnight = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
  const days = Math.round((midnight(now) - midnight(when)) / 86400000)
  if (days <= 1) return 'yesterday'
  if (days < 7) return `${days} days ago`
  return when.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** D235: "Playground 1", or the first number not taken by the investigation's playgrounds. */
/** A file or folder size: "512 B", "84 kB", "3.2 MB", "1.25 GB". */
export function byteSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} kB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

export function nextPlaygroundName(taken: string[]): string {
  for (let n = 1; ; n++) if (!taken.includes(`Playground ${n}`)) return `Playground ${n}`
}
