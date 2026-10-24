// D182 (UI10): what an empty view says. An icon, one line naming what is missing, two sentences on
// what the view is for, and the ways to start as buttons, which call what the view's own buttons do.

import type { ReactNode } from 'react'
import { Icon, type IconName } from '../icons'

export function EmptyState({
  icon,
  title,
  children,
  actions,
  tip,
  compact = false,
  className = '',
}: {
  icon: IconName
  title: string
  /** The explanation: two sentences. */
  children: ReactNode
  actions?: ReactNode
  tip?: ReactNode
  /** Smaller, for a short area such as the energy drawer. */
  compact?: boolean
  className?: string
}) {
  return (
    <section className={`empty-state${compact ? ' compact' : ''} ${className}`.trim()} aria-label={title}>
      <div className="empty-icon">
        <Icon name={icon} />
      </div>
      <h3>{title}</h3>
      <p>{children}</p>
      {actions && <div className="buttons">{actions}</div>}
      {tip && <p className="empty-tip">{tip}</p>}
    </section>
  )
}
