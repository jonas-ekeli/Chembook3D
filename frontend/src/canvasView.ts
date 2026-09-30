/** D43: compact (label and status), energy (label and ΔX from the reference), structure. */
export type ViewMode = 'compact' | 'energy' | 'structure'

/** Hidden values per filter; 'none' stands for "no branch" or "no step". Filters only hide
 * items and never change data (FR-CAN-05, T-UI-02). */
export type Filters = { branches: string[]; statuses: string[]; steps: string[] }
export const NO_FILTERS: Filters = { branches: [], statuses: [], steps: [] }

export type Selection = { kind: 'node' | 'group' | 'edge'; id: string } | null
