import type { CSSProperties, ReactNode } from 'react'
import { STATUS_LABEL, type Node } from '../api'

/** D178: what a node list row shows, on one line: its branch's colour as a bar, a status dot in
 * the colours of the node cards (D175), the label with the amber "TS" tag of the cards, and the
 * formula at the right. The app's outline and the read-only copy share it. */
export function NodeRow({ node, colour, children }: { node: Node; colour?: string; children?: ReactNode }) {
  return (
    <>
      <span className="branch-bar" style={colour ? ({ '--branch': colour } as CSSProperties) : undefined} aria-hidden="true" />
      <span className={`status-dot status-${node.status}`} title={STATUS_LABEL[node.status]} aria-hidden="true" />
      <span className="node-label">
        {node.derived_from_id && <span title="Derived node">↳ </span>}
        <span className="node-name">{node.label || <em>Untitled node</em>}</span>
        {node.warnings.length > 0 && (
          <span
            className="warn-icon"
            role="img"
            aria-label="Has warnings"
            title={node.warnings.map((w) => `${w.code}: ${w.message}`).join('\n')}
          >
            ⚠
          </span>
        )}
        {node.role === 'transition_state' && (
          <span className="ts-mark" title="Transition state">
            TS
          </span>
        )}
      </span>
      {children}
      {node.formula && <span className="node-formula">{node.formula}</span>}
      <span className="visually-hidden">{STATUS_LABEL[node.status]}</span>
    </>
  )
}
