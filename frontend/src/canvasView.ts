import type { Canvas } from './api'

/** D43: compact (label and status), energy (label and ΔX from the reference), structure. */
export type ViewMode = 'compact' | 'energy' | 'structure'

/** Hidden values per filter; 'none' stands for "no branch" or "no step". Filters only hide
 * items and never change data (FR-CAN-05, T-UI-02). */
export type Filters = {
  branches: string[]
  statuses: string[]
  steps: string[]
  /** D264b: "To review": only Claude's open proposals and their ends. Not saved with the view. */
  review?: boolean
}
export const NO_FILTERS: Filters = { branches: [], statuses: [], steps: [] }

/** How many filters hide something: each hidden value, and "To review" (D264b). */
export const hiddenCount = (filters: Filters) =>
  filters.branches.length + filters.statuses.length + filters.steps.length + (filters.review ? 1 : 0)

/** D264: how many edges Claude proposed are still open on the canvas. */
export const openProposals = (canvas: Canvas) => canvas.transitions.filter((t) => t.review === 'open').length

/** D193: 'sub' is a sub-investigation's box. */
export type Selection = { kind: 'node' | 'group' | 'edge' | 'sub'; id: string } | null
