// The profile chart's pure maths (ProfileChart.tsx): the y axis and the text that has to fit.
// Kept apart from the component so the unit tests can load it without React.

/** The y axis's range for values in the display unit: from the lowest to the highest, zero (the
 * reference) always included, with 12% of the span added at each end (1 unit's 12% when every
 * value is the same). */
export function valueRange(shown: number[]): { lo: number; hi: number } {
  const all = [0, ...shown]
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const pad = (hi - lo || 1) * 0.12
  return { lo: lo - pad, hi: hi + pad }
}

/** Round numbers for the y axis. */
export function ticks(min: number, max: number, count = 5): number[] {
  const span = max - min || 1
  const raw = span / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw
  const found = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) found.push(Number(v.toFixed(10)))
  return found
}

/** The fewest decimals that write every tick exactly. */
export function tickDecimals(values: number[]): number {
  for (let d = 0; d < 6; d++) if (values.every((v) => Number(v.toFixed(d)) === v)) return d
  return 6
}

let measure: CanvasRenderingContext2D | null | undefined

/** The width of `text` in the figure's font, measured by the browser; estimated where it
 * cannot measure. */
export function textWidth(text: string, size: number, family: string, weight = 400): number {
  if (measure === undefined) {
    try {
      measure = document.createElement('canvas').getContext('2d')
    } catch {
      measure = null
    }
  }
  if (!measure) return text.length * size * 0.6
  measure.font = `${weight} ${size}px ${family}`
  return measure.measureText(text).width
}

/** D198: `text` broken into lines no wider than `width`, after a space, `_`, `-`, `/` or `.`
 * where it can, and inside a word only when the word alone is wider than a line. The lines joined
 * give the text back (a space at a break ends its line and is not counted in its width). */
export function wrapText(text: string, width: number, size: number, family: string): string[] {
  const fits = (line: string) => textWidth(line.trimEnd(), size, family) <= width
  if (fits(text)) return [text]
  const lines: string[] = []
  let line = ''
  for (const piece of text.match(/[^ _\-/.]+[ _\-/.]*|[ _\-/.]+/g) ?? []) {
    if (fits(line + piece)) {
      line += piece
      continue
    }
    // A piece wider than a line is cut where the line is full; any other starts a new line.
    if (fits(piece) && line) {
      lines.push(line)
      line = ''
    }
    for (const char of piece) {
      if (line && !fits(line + char)) {
        lines.push(line)
        line = ''
      }
      line += char
    }
  }
  if (line) lines.push(line)
  return lines
}
