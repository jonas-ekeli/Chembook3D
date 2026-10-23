// Small helpers shared by the app and its read-only copy (D79).

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

export function download(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
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
export function tableCellClass(column: number, columns: number): string | undefined {
  if (column === columns - 1) return 'check'
  return column >= 4 ? 'num' : undefined
}

// D83, D86, D171: shared by the analyses in the Analyses view.
export function errorText(err: unknown): string {
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
