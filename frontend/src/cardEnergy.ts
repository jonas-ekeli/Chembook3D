import { balanceText, formatDelta, type EnergyView, type Settings } from './api'

/** What the canvas shows of the energy view (FR-CAN-03, FR-CAN-04 energy mode). */
export type CanvasEnergy = {
  view: EnergyView | null
  type: string
  showEdges: boolean
  referenceId: string | null
  settings: Settings | null
}

/** D175: a card's energy, "ΔG" and the number apart. No number is a quiet "—", or a short
 * `missing` note when every card lacks one for the same reason (no reference chosen). */
export type CardEnergy = { type: string; value: string | null; title: string; missing?: string }

/** The ΔX a node or group card shows (D43: from the reference node, never an absolute value;
 * D72: the server balances it with every free species that joined or left between the reference
 * and the node, so it matches the profile along that route). The node panel's stat strip shows
 * the same (D179). Null without an energy view. */
export function cardEnergy(energy: CanvasEnergy | null, id: string): CardEnergy | null {
  if (!energy?.view) return null
  const type = `Δ${energy.type}`
  if (!energy.referenceId) return { type, value: null, title: 'Choose a reference node', missing: 'no reference' }
  if (energy.view.reference_id !== energy.referenceId) return { type, value: '…', title: 'Updating' }
  const own = energy.view.relative[id]
  if (own?.value == null) return { type, value: null, title: `${type} n/a: ${own?.message ?? 'no value'}` }
  const unit = energy.settings?.energy_unit ?? 'kcal/mol'
  const title = own.species.length
    ? `${unit}, balanced with ${balanceText(own.species)}`
    : own.joined
      ? unit
      : `${unit}; not joined to the reference by transitions, so no free species are counted`
  return { type, value: formatDelta(own.value, energy.settings), title }
}

/** A number as shown: a real minus sign (D175). */
export function minus(text: string): string {
  return text.replace(/^-/, '−')
}
