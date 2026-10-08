import type { AtomMatch } from './api'

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** D113: one line saying what the match found. */
export function describeMatch(match: AtomMatch): string {
  const changes = [
    match.formed.length ? `${count(match.formed.length, 'bond forms', 'bonds form')}` : '',
    match.broken.length ? `${count(match.broken.length, 'bond breaks', 'bonds break')}` : '',
    match.inverted.length ? `${count(match.inverted.length, 'centre inverts', 'centres invert')}` : '',
  ].filter(Boolean)
  const what = changes.length ? changes.join(', ') : 'no bond forms or breaks'
  const how = match.same_numbering ? 'The two structures already share their numbering' : 'Atoms matched'
  return `${how}: ${what} (RMSD ${match.rmsd.toFixed(2)} Å after fitting).`
}
