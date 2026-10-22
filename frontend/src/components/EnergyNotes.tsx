import type { EnergyNote } from '../api'

/** D160: W-TEMP and W-STDSTATE of the energies shown. As badges (the energy view's bar), each
 * with its message as the tooltip, or as a list (above the profile and the table). */
export function EnergyNotes({ notes, badges = false }: { notes?: EnergyNote[] | null; badges?: boolean }) {
  if (!notes?.length) return null
  if (badges) {
    return (
      <>
        {notes.map((n) => (
          <span key={n.code} className="badge warn" title={n.message} aria-label={`${n.code}: ${n.message}`}>
            ⚠ {n.code}
          </span>
        ))}
      </>
    )
  }
  return (
    <ul className="warnings energy-notes" aria-label="Energy notes">
      {notes.map((n) => (
        <li key={n.code}>
          <span className="badge warn">{n.code}</span> {n.message}
        </li>
      ))}
    </ul>
  )
}
