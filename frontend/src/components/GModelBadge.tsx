import { gModelBadge } from '../api'

/** D163: which thermochemistry model the printed G comes from (RRHO, qRRHO, mRRHO), beside the
 * energy type, the drawer's heading and in the read-only copy; ⚠ when one level mixes them. */
export function GModelBadge({ models }: { models: string[] | undefined }) {
  const badge = gModelBadge(models)
  if (!badge) return null
  return (
    <span className="badge" aria-label="Model of the printed G" title={badge.title}>
      {badge.text}
    </span>
  )
}
