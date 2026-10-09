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
