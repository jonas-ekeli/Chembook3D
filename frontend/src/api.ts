// Thin client for the local backend API (src/chembook3d/api/routes.py). All rules live in
// the backend; the UI only shows what it returns.

import type { HydrogenMode, Rotation } from './chem'
import type { StericColours } from './display'

export type Status = 'planned' | 'running_externally' | 'done' | 'failed' | 'rejected' | 'superseded'
export type Role = 'minimum' | 'transition_state' | 'unspecified'

export const STATUSES: { value: Status; label: string }[] = [
  { value: 'planned', label: 'Planned' },
  { value: 'running_externally', label: 'Running externally' },
  { value: 'done', label: 'Done' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'superseded', label: 'Superseded' },
]

export const ROLES: { value: Role; label: string }[] = [
  { value: 'unspecified', label: 'Unspecified' },
  { value: 'minimum', label: 'Minimum' },
  { value: 'transition_state', label: 'Transition state' },
]

export type SyncState =
  | 'not_linked'
  | 'no_git'
  | 'up_to_date'
  | 'not_pushed'
  | 'behind'
  | 'diverged'
  | 'unreachable'
  | 'error'

/** Git sync state of an investigation (D71). */
export type SyncStatus = {
  state: SyncState
  message: string
  remote: string | null
  local: { when: string; summary: string } | null
  upstream: { when: string; summary: string } | null
}

export type SyncConflict = SyncStatus & { folder: string }

/** States in which a sync did not get through; the app says why. */
export const SYNC_PROBLEMS: SyncState[] = ['no_git', 'unreachable', 'error']

/** GitHub refuses files over 100 MB (FR-SYNC-09). */
export const LARGE_FILE = 100 * 1024 * 1024

export type Investigation = {
  name: string
  folder: string
  /** Linked to a Git repository (D71). */
  linked: boolean
  /** What the pull on open found, when linked. */
  sync: SyncStatus | null
}

export type CalculationType =
  | 'optimization'
  | 'ts_optimization'
  | 'frequency'
  | 'single_point'
  | 'conformer_search'
  | 'other'

export const CALCULATION_TYPES: Record<string, string> = {
  optimization: 'Optimization',
  ts_optimization: 'TS optimization',
  frequency: 'Frequency',
  single_point: 'Single point',
  conformer_search: 'Conformer search',
  other: 'Other',
}

/** An automatic, non-blocking finding (docs/spec/02 §4); never changes a status. */
export type Finding = { code: string; message: string; calculation_id?: string | null }

/** D69: a pathway node, or a free species (substrate or fragment) kept off the canvas. */
export type NodeKind = 'node' | 'species'

export type Node = {
  id: string
  label: string
  kind: NodeKind
  role: Role
  charge: number | null
  multiplicity: number | null
  status: Status
  tags: string[]
  notes: string
  formula: string | null
  atom_count: number
  xyz: string | null
  derived_from_id: string | null
  calculation_count: number
  warnings: Finding[]
  step_id: string | null
  branch_id: string | null
  group_id: string | null
  origin_branch_id: string | null
  pos_x: number
  pos_y: number
  /** The orientation saved from the 3D view, used by the structure-mode card. */
  view_rotation: Rotation | null
  created_at: string
  updated_at: string
}

export type NodeFields = Partial<
  Pick<
    Node,
    | 'label'
    | 'role'
    | 'charge'
    | 'multiplicity'
    | 'status'
    | 'tags'
    | 'notes'
    | 'step_id'
    | 'branch_id'
    | 'view_rotation'
  >
>

/** A conceptual position in the mechanism, shared by all branches (D3, P2). */
export type Step = { id: string; name: string; notes: string; position: number; node_count: number }

/** A named, coloured lineage (D12). `lineage_paths` lists every chain of parents to a root. */
export type Branch = {
  id: string
  name: string
  colour: string
  status: Status
  notes: string
  parent_ids: string[]
  child_ids: string[]
  lineage_paths: string[][]
  split_node_id: string | null
  node_count: number
}

export type BranchFields = Partial<Pick<Branch, 'name' | 'colour' | 'status' | 'notes' | 'parent_ids'>>

/** A directed edge between nodes or group nodes (D3). `direct`: no TS at either end (D53). */
export type Transition = {
  id: string
  source_id: string
  target_id: string
  source_kind: 'node' | 'group'
  target_kind: 'node' | 'group'
  status: Status
  notes: string
  direct: boolean
  cross_branch: boolean
  /** D76: the side of each box the arrow leaves from and arrives at. */
  source_side: Side
  target_side: Side
  /** D69: free species that join or leave on this transition. */
  species: TransitionSpecies[]
  /** W-BALANCE when the atoms or the charge do not balance. */
  warnings: Finding[]
}

export type TransitionSides = Partial<Pick<Transition, 'source_side' | 'target_side'>>

export type SpeciesDirection = 'joins' | 'leaves'

/** D76: the four sides of a node or group box an arrow can use. */
export const SIDES = ['top', 'right', 'bottom', 'left'] as const
export type Side = (typeof SIDES)[number]
export const isSide = (value: unknown): value is Side => SIDES.includes(value as Side)

/** Chip text for a species on an edge: "+ propene" joins, "− 2 × C₂H₄" leaves (D69). */
export function speciesChip(entry: { direction: SpeciesDirection; count: number; label: string }): string {
  return `${entry.direction === 'joins' ? '+' : '−'} ${entry.count > 1 ? `${entry.count} × ` : ''}${entry.label}`
}

/** The species that balance a point, "+ ethylene − 2 × styrene" (D69). */
export function balanceText(species: SpeciesCount[]): string {
  return species
    .map((s) => `${s.count > 0 ? '+' : '−'} ${Math.abs(s.count) > 1 ? `${Math.abs(s.count)} × ` : ''}${s.label}`)
    .join(' ')
}

export type SpeciesCount = { species_id: string; label: string; count: number }

export type TransitionSpecies = {
  species_id: string
  label: string
  formula: string | null
  direction: SpeciesDirection
  count: number
}

/** A reconnection or conformer group (D18): members and a user-chosen representative. */
export type Group = {
  id: string
  label: string
  notes: string
  step_id: string | null
  member_ids: string[]
  representative_id: string | null
  incoming_branch_ids: string[]
  outgoing_branch_id: string | null
  pos_x: number
  pos_y: number
  /** How the members are laid out when the group is expanded (A21). */
  layout: GroupLayout
}

export type GroupLayout = 'grid' | 'vertical' | 'horizontal'

export type GroupFields = Partial<
  Pick<Group, 'label' | 'notes' | 'step_id' | 'representative_id' | 'outgoing_branch_id' | 'pos_x' | 'pos_y' | 'layout'>
>

export type Canvas = {
  nodes: Node[]
  /** Free species (D69), listed beside the canvas rather than drawn on it. */
  species: Node[]
  steps: Step[]
  branches: Branch[]
  transitions: Transition[]
  groups: Group[]
  /** D85: notes pinned to a corner of a node's card. */
  notes: Note[]
}

export const NOTE_CORNERS = ['top-left', 'top-right', 'bottom-left', 'bottom-right'] as const
export type NoteCorner = (typeof NOTE_CORNERS)[number]
export const NOTE_CORNER_LABEL: Record<NoteCorner, string> = {
  'top-left': 'Top left',
  'top-right': 'Top right',
  'bottom-left': 'Bottom left',
  'bottom-right': 'Bottom right',
}
export const NOTE_COLOURS = ['yellow', 'blue', 'green', 'pink', 'grey'] as const
export type NoteColour = (typeof NOTE_COLOURS)[number]
/** D87: on the corner, or floating apart from it with or without a line to it. */
export const NOTE_PLACEMENTS = ['corner', 'line', 'free'] as const
export type NotePlacement = (typeof NOTE_PLACEMENTS)[number]
export const NOTE_PLACEMENT_LABEL: Record<NotePlacement, string> = {
  corner: 'On the corner',
  line: 'Floating, with a line to the corner',
  free: 'Floating, no line',
}

/** D85: a note pinned to a node's card. `body` is cleaned HTML (services/notes.py); a
 * picture in it is `<img data-note-image="<sha256>">`. */
export type Note = {
  id: string
  node_id: string
  corner: NoteCorner
  colour: NoteColour
  title: string
  body: string
  collapsed: boolean
  width: number
  /** D87: where it is drawn; floating, its corner facing the card's corner sits `offset_x`,
   * `offset_y` px outward from it, and it moves with the card. */
  placement: NotePlacement
  offset_x: number
  offset_y: number
  /** Set once resized; null fits its text. */
  height: number | null
  created_at: string
  updated_at: string
}

export type NoteLayout = Pick<Note, 'collapsed' | 'width' | 'height' | 'placement' | 'offset_x' | 'offset_y'>
export type NoteFields = Partial<Pick<Note, 'corner' | 'colour' | 'title' | 'body'> & NoteLayout>

export type NodeDeletePreview = {
  node: string
  calculations: number
  transitions: { id: string; source_id: string; target_id: string }[]
  /** D69: transitions a free species is taken off when it is deleted. */
  species_on: { id: string; source_id: string; target_id: string }[]
}

export type GroupDeletePreview = {
  members: { id: string; label: string; calculation_count: number }[]
  calculations: number
  group_transitions: number
  all_transitions: number
}

export type Modes = { xyz: string; frequencies: number[]; order: number[]; modes: number[][][] }

/** D80: all atoms (when they correspond), chosen atoms, or the stored coordinates. */
export type OverlayAlign = 'all' | 'atoms' | 'none'

export type OverlayRequest = {
  node_ids: string[]
  reference_id?: string | null
  align: OverlayAlign
  /** 1-based atom numbers of the full structure, per node, paired in order. */
  atoms?: Record<string, number[]>
  allow_mirror?: boolean
}

export type OverlayStructure = {
  node_id: string
  label: string
  /** As placed on the reference. */
  xyz: string
  reference: boolean
  /** False when only the centres were matched, or nothing was moved. */
  rotated: boolean
  rmsd_atoms: number | null
  rmsd_all: number | null
  mirrored: boolean
}

export type Overlay = { align: OverlayAlign; structures: OverlayStructure[] }

/** FR-3D-07: a named list of alignment atoms per node. */
export type AlignmentSet = { id: string; name: string; atoms: Record<string, number[]> }

/** D81: one node's atoms in a steric profile, 1-based numbers of the full structure. The
 * z-axis and xz-plane atoms orient the sphere (both or neither). */
export type StericAtoms = { centre: number[]; z_axis: number[]; xz_plane: number[]; excluded: number[] }

export type StericRadii = 'bondi' | 'crc'

export type StericSettings = {
  radius: number
  radii: StericRadii
  radii_scale: number
  include_hydrogens: boolean
  mesh: number
  /** The map's colour scale runs from −limit to +limit Å; null means the sphere radius. */
  map_limit: number | null
}

export type StericValues = {
  buried_percent: number
  buried_volume: number
  sphere_volume: number
  atoms_counted: number
  /** NE, NW, SW, SE; null without an orientation. */
  quadrants: Record<string, number> | null
  /** NE+, NE-, …: the quadrant above (+) or below (−) the xy-plane. */
  octants: Record<string, number> | null
  /** Elements the radii table lacks; morfeus uses 2.0 Å for them. */
  missing_radii: string[]
}

export type StericProfile = StericSettings & {
  id: string
  name: string
  atoms: Record<string, StericAtoms>
  /** The last result per node; `stale` says why it is out of date. */
  results: Record<string, { values: StericValues; computed_at: string | null; stale: string | null }>
}

/** A steric map: heights z (Å) over the grid, rows along y from −r, columns along x. */
export type StericMap = { x: number[]; z: (number | null)[][]; limit: number }

export type StericComputed = {
  node_id: string
  label: string
  values: StericValues | null
  map: StericMap | null
  error: string | null
  computed_at: string | null
}

export type StericProfileFields = Partial<StericSettings> & {
  name?: string
  atoms?: Record<string, StericAtoms | { same_as: string } | null>
}

export type HistoryEntry = {
  id: number
  timestamp: string
  record_type: string
  record_id: string
  // split, reconnect, add_members, remove_member and dissolve are the structural actions INV-3
  // records as one entry each
  action: 'create' | 'update' | 'delete' | 'split' | 'reconnect' | 'add_members' | 'remove_member' | 'dissolve'
  field: string | null
  old_value: unknown
  new_value: unknown
  source: string
}

export type Settings = {
  energy_unit: string
  energy_units: string[]
  energy_factors: Record<string, number>
  recent: string[]
  last_device: string
  /** D78: the folder a file was last picked from in the import dialog ("" when none or gone). */
  last_import_folder: string
  geometry_tolerance: number
  duplicate_tolerance: number
  energy_decimals: Record<string, number>
  qh_temperature: number
  qh_cutoff: number
  /** D34: how many of the lowest CREST conformers are ticked on import. */
  crest_count: number
  /** Hydrogens drawn in the 3D views and on structure cards. */
  hydrogens: HydrogenMode
  hydrogen_modes: HydrogenMode[]
  /** D84: the colours of the steric maps. */
  steric_colours: StericColours
}

export type Level = {
  id: string
  label: string
  program: string
  method: string
  basis: string
  dispersion: string
  solvation_model: string
  solvent: string
}

export type LevelFields = Pick<Level, 'method' | 'basis' | 'dispersion' | 'solvation_model' | 'solvent'>

export type SourceFile = {
  id: string
  stored_path: string
  original_name: string
  origin_device: string
  origin_path: string
  checksum: string
  size: number
  imported_at: string
  exists: boolean
}

export type CalculationResult = {
  energy: number | null
  zpe: number | null
  e_corr: number | null
  h_corr: number | null
  g_corr: number | null
  e_zpe: number | null
  e_thermal: number | null
  h: number | null
  g: number | null
  temperature: number | null
  pressure: number | null
  molecular_mass: number | null
  symmetry_number: number | null
  point_group: string | null
  rotational_temperatures: number[] | null
  frequencies: number[]
  imaginary_count: number | null
  optimization_converged: boolean | null
  geometry_count: number
  printed: Record<string, string>
}

export type Calculation = {
  id: string
  node_id: string
  type: CalculationType
  program: string
  program_version: string
  level: Level | null
  geometry_level: Level | null
  composite_label: string
  parsed_level: Record<string, unknown> | null
  level_edited: boolean
  route: string
  title: string
  step_index: number | null
  step_count: number | null
  termination: 'normal' | 'abnormal' | 'unknown'
  charge: number | null
  multiplicity: number | null
  parse_warnings: string[]
  warnings: Finding[]
  source_file: SourceFile | null
  result: CalculationResult | null
  quasi_harmonic: QuasiHarmonic | null
  notes: string
  created_at: string
}

/** FR-EN-04: the G_qh correction recomputed from a frequency step, next to the printed one. */
export type QuasiHarmonic = {
  temperature: number
  cutoff: number
  correction: number | null
  code: string | null
  message: string | null
  raised_modes: number | null
  imaginary_excluded: number | null
}

export type EnergyType = 'E' | 'H' | 'G' | 'G_qh'

/** D83: a saved selectivity. Members are node ids (one TS) or group ids (all members). */
export type SelectivityOutcome = { id?: string; name: string; members: string[]; experimental: number | null }

export type Selectivity = {
  id: string
  name: string
  /** A composite level key, or null until one is chosen. */
  level: string | null
  energy_type: EnergyType
  /** K; null follows the G_qh temperature setting. */
  temperature: number | null
  conformers: 'boltzmann' | 'lowest'
  excess: 'ee' | 'de' | 'none'
  notes: string
  outcomes: SelectivityOutcome[]
}

export type SelectivityFields = Partial<Omit<Selectivity, 'id'>>

export type SelectivityMemberResult = {
  node_id: string
  label: string
  group_id: string | null
  group_label: string | null
  role: Role
  /** Absolute, hartree. */
  value: number | null
  /** From the lowest TS of all outcomes, hartree. */
  relative: number | null
  /** Percent of all TSs, and of this outcome's. */
  share: number | null
  share_in_outcome: number | null
  message: string | null
}

export type SelectivityOutcomeResult = {
  id: string
  name: string
  experimental: number | null
  members: SelectivityMemberResult[]
  /** ΔΔG‡ in hartree from the lowest outcome, and the predicted percentage, both ways (S2). */
  boltzmann_ddg: number | null
  boltzmann_percent: number | null
  lowest_ddg: number | null
  lowest_percent: number | null
  experimental_percent: number | null
  experimental_ddg: number | null
}

export type Excess = { value: number; major: string } | null

export type SelectivityResult = {
  status: 'ok' | 'n/a' | 'refused' | 'incomplete'
  message: string | null
  level: string | null
  level_label: string | null
  energy_type: EnergyType
  temperature: number
  temperature_from_settings: boolean
  cutoff: number
  conformers: 'boltzmann' | 'lowest'
  outcomes: SelectivityOutcomeResult[]
  excess: { label: 'ee' | 'de'; boltzmann: Excess; lowest: Excess; experimental: Excess } | null
  notes: string[]
}

/** D86: a saved turnover, the TOF of one closed cycle (A13) from the energetic-span model. */
export type Turnover = {
  id: string
  name: string
  /** Node and group ids; the last returns to an earlier one. Empty until one is chosen. */
  path: string[]
  level: string | null
  energy_type: EnergyType
  /** K; null follows the G_qh temperature setting. */
  temperature: number | null
  compare_id: string | null
  notes: string
}

export type TurnoverFields = Partial<Omit<Turnover, 'id'>>

export type TurnoverPoint = {
  id: string
  label: string
  kind: 'node' | 'group'
  is_ts: boolean
  /** From the cycle's first node, balanced by the free species, hartree. */
  relative: number | null
  /** Degree of TOF control, 0 to 1. */
  control: number | null
}

export type TurnoverResult = {
  status: 'ok' | 'n/a' | 'refused' | 'incomplete'
  message: string | null
  level: string | null
  level_label: string | null
  energy_type: EnergyType
  temperature: number
  temperature_from_settings: boolean
  cutoff: number
  unit: string
  cycle: string[]
  points: TurnoverPoint[]
  /** ΔG_r and δE in hartree; TOF in 1/s. */
  reaction: number | null
  tof: number | null
  tof_span: number | null
  span: number | null
  /** Indices into points. */
  tdts: number | null
  tdi: number | null
  profile: Profiles | null
  table: EnergyTable | null
  comparison: {
    id: string
    name: string
    tof: number | null
    ratio: number | null
    percent: number | null
    ddg: number | null
    message: string | null
  } | null
  notes: string[]
}

/** FR-EN-01: a composite level and the energy types that have at least one value there. */
export type EnergyOptions = {
  levels: { key: string; label: string; types: EnergyType[] }[]
  temperature: number
  cutoff: number
}

/** One node's or group's energy in hartree, or null with the reason (EN-3: no fallback). */
export type EnergyValue = {
  value: number | null
  code: string | null
  message: string | null
  energy_calculation_id: string | null
  thermo_calculation_id: string | null
  details: Record<string, unknown>
}

export type EnergyView = {
  level: string
  type: EnergyType
  values: Record<string, EnergyValue>
  edges: Record<string, { delta: number | null; direct: boolean; message?: string | null }>
  reference_id: string | null
  /** ΔX of each node and group from the reference, with the species that balance it (D72). */
  relative: Record<string, { value: number | null; species: SpeciesCount[]; message: string | null; joined: boolean }>
}

export type PathChoice = { transition_id: string; node_id: string; label: string; status: string }
export type Pathway = { path: string[]; choices: PathChoice[]; closed: boolean }

export type ProfilePoint = EnergyValue & {
  id: string
  kind: 'node' | 'group'
  label: string
  is_ts: boolean
  step_id: string | null
  step_name: string | null
  step_position: number | null
  branch_id: string | null
  relative: number | null
  /** D69: free species added (count > 0) or subtracted (count < 0) to balance this point. */
  species: SpeciesCount[]
  species_message: string | null
}

export type Profiles = {
  level: string
  level_label: string
  type: EnergyType
  reference_id: string
  reference_value: number | null
  temperature: number
  cutoff: number
  profiles: {
    points: ProfilePoint[]
    segments: { transition_id: string; forward: boolean; status: string; direct: boolean }[]
  }[]
}

export type ProfileRequest = {
  paths: string[][]
  reference_id: string | null
  level: string
  type: EnergyType
  unit?: string
}

export type EnergyTable = { columns: string[]; rows: string[][] }

export type StepPlan = {
  index: number
  type: CalculationType
  title: string | null
  route: string | null
  level_label: string
  geometry_level_label: string | null
  termination: string
  optimization_converged: boolean | null
  energy: number | null
  charge: number | null
  multiplicity: number | null
  atom_count: number
  geometry_count: number
  frequency_count: number
  imaginary_count: number | null
  lowest_frequencies: number[]
  thermo: Record<string, number | string>
  rmsd_to_node: number | null
  assignment: 'node' | 'derived' | 'preview'
  missing: string[]
}

export type NameRequest = {
  kind: 'basis' | 'dispersion'
  key: string
  description: string
  elements: string[]
  title: string | null
  name: string | null
  recognised: boolean
}

/** What an import would write; nothing is stored until it is committed (FR-IMP-05). */
export type ImportPlan = {
  token: string
  program: string
  program_version: string | null
  original_name: string
  size: number
  checksum: string
  mode: 'new' | 'planned' | 'onto'
  target_node_id: string | null
  target_label: string | null
  chosen_step: number | null
  steps: StepPlan[]
  xyz: string | null
  derived_offered: boolean
  duplicates: { node_id: string; label: string; rmsd: number }[]
  already_imported: { original_name: string; imported_at: string; nodes: string[] }[]
  names: NameRequest[]
  warnings: Finding[]
  suggested: { label: string; role: Role; status: Status }
  origin: { device: string; path: string; name: string }
  blockers: string[]
  /** 'ensemble' for a CREST conformer ensemble (FR-IMP-10), which has no job steps. */
  kind: 'steps' | 'ensemble'
  ensemble: EnsemblePlan | null
}

export type EnsembleConformer = {
  /** Position in the file, 1-based. */
  index: number
  energy: number
  /** Hartree above the lowest conformer. */
  relative: number
  selected: boolean
}

export type EnsemblePlan = {
  count: number
  method: string
  level_label: string
  charge: number | null
  multiplicity: number | null
  atom_count: number
  selected_count: number
  conformers: EnsembleConformer[]
}

export type ImportOptions = {
  target_node_id?: string | null
  step?: number | null
  duplicate_action?: 'attach' | 'new' | null
  duplicate_node_id?: string | null
  create_derived?: boolean
  basis_names?: Record<string, string>
  dispersion_names?: Record<string, string>
  label?: string | null
  kind?: NodeKind | null
  role?: Role | null
  status?: Status | null
  origin_device?: string | null
  origin_path?: string | null
  original_name?: string | null
  pos_x?: number | null
  pos_y?: number | null
  conformer_count?: number | null
  conformers?: number[] | null
  method?: string | null
  charge?: number | null
  multiplicity?: number | null
}

export type ImportResult = {
  node_id: string | null
  derived_node_id: string | null
  calculation_ids: string[]
  group_id: string | null
}

/** FR-OV-01: the resume overview. */
export type BranchSummary = {
  id: string | null
  name: string
  colour: string | null
  status: Status | null
  counts: Partial<Record<Status, number>>
  node_count: number
}

export type OpenItem = {
  kind: 'node' | 'transition' | 'group'
  id: string
  label: string
  reason: 'failed' | 'running_externally' | 'planned' | 'warning' | 'direct'
  detail: string
  branch_ids: string[]
}

export type Overview = {
  branches: BranchSummary[]
  open_items: OpenItem[]
  recent: HistoryEntry[]
  steps: { id: string; name: string; position: number; notes: string }[]
  group_count: number
}

export type FolderListing = {
  path: string
  parent: string | null
  is_investigation: boolean
  entries: { name: string; path: string; is_investigation: boolean; is_file: boolean; size: number | null }[]
  roots: string[]
}

export type XyzLineError = { line: number; message: string }

/** An error response from the backend, with its parsed `detail`. */
export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    const message =
      typeof detail === 'string'
        ? detail
        : detail && typeof (detail as { message?: unknown }).message === 'string'
          ? (detail as { message: string }).message
          : `Request failed (HTTP ${status})`
    super(message)
    this.status = status
    this.detail = detail
  }

  get xyzErrors(): XyzLineError[] | null {
    const detail = this.detail as { xyz_errors?: XyzLineError[] } | null
    return detail && Array.isArray(detail.xyz_errors) ? detail.xyz_errors : null
  }

  get blockers(): string[] | null {
    const detail = this.detail as { blockers?: string[] } | null
    return detail && Array.isArray(detail.blockers) ? detail.blockers : null
  }

  get lock(): { host?: string; opened_at?: string } | null {
    const detail = this.detail as { locked?: { host?: string; opened_at?: string } } | null
    return this.status === 409 && detail && detail.locked ? detail.locked : null
  }

  get syncConflict(): SyncConflict | null {
    const detail = this.detail as { sync_conflict?: SyncConflict } | null
    return this.status === 409 && detail && detail.sync_conflict ? detail.sync_conflict : null
  }

  get needsUpgrade(): { from: string; to: string } | null {
    const detail = this.detail as { needs_upgrade?: { from: string; to: string } } | null
    return this.status === 409 && detail && detail.needs_upgrade ? detail.needs_upgrade : null
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const raw = body instanceof Blob
  const response = await fetch(`/api${path}`, {
    method,
    headers:
      body === undefined
        ? undefined
        : { 'Content-Type': raw ? 'application/octet-stream' : 'application/json' },
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
  })
  if (!response.ok) {
    let detail: unknown = null
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  currentInvestigation: () => request<Investigation | null>('GET', '/investigation'),
  createInvestigation: (folder: string, name: string) =>
    request<Investigation>('POST', '/investigations', { folder, name }),
  openInvestigation: (folder: string, force = false, upgrade = false) =>
    request<Investigation>('POST', '/investigations/open', { folder, force, upgrade }),
  cloneInvestigation: (url: string, folder: string) =>
    request<Investigation>('POST', '/investigations/clone', { url, folder }),
  /** Closing a linked investigation also pushes it; the answer is the sync state (D71). */
  closeInvestigation: () => request<SyncStatus | null>('POST', '/investigations/close'),
  syncStatus: () => request<SyncStatus>('GET', '/sync'),
  syncNow: () => request<SyncStatus>('POST', '/sync'),
  linkRepository: (url: string) => request<SyncStatus>('POST', '/sync/link', { url }),
  resolveSync: (folder: string, keep: 'this' | 'github') =>
    request<SyncStatus>('POST', '/sync/resolve', { folder, keep }),
  folders: (path?: string, files = false) => {
    const query = new URLSearchParams()
    if (path) query.set('path', path)
    if (files) query.set('files', 'true')
    const text = query.toString()
    return request<FolderListing>('GET', text ? `/folders?${text}` : '/folders')
  },

  nodes: () => request<Node[]>('GET', '/nodes'),
  setNodeKind: (id: string, kind: NodeKind) => request<Node>('PUT', `/nodes/${id}/kind`, { kind }),
  createNode: (fields: NodeFields & { kind?: NodeKind; xyz?: string; pos_x?: number; pos_y?: number }) =>
    request<Node>('POST', '/nodes', fields),
  updateNode: (id: string, fields: NodeFields) => request<Node>('PATCH', `/nodes/${id}`, fields),
  setGeometry: (id: string, xyz: string) =>
    request<{ node: Node; derived: boolean }>('PUT', `/nodes/${id}/geometry`, { xyz }),
  deletePreview: (id: string) => request<NodeDeletePreview>('GET', `/nodes/${id}/delete-preview`),
  deleteNode: (id: string) => request<unknown>('DELETE', `/nodes/${id}`),
  xyzDownloadUrl: (id: string) => `/api/nodes/${id}/xyz`,

  history: (recordId?: string) =>
    request<HistoryEntry[]>(
      'GET',
      recordId ? `/history?record_id=${encodeURIComponent(recordId)}` : '/history',
    ),

  overview: (since?: string | null) =>
    request<Overview>('GET', since ? `/overview?since=${encodeURIComponent(since)}` : '/overview'),

  settings: () => request<Settings>('GET', '/settings'),
  saveSettings: (
    fields: Partial<
      Pick<
        Settings,
        | 'energy_unit'
        | 'last_device'
        | 'geometry_tolerance'
        | 'duplicate_tolerance'
        | 'qh_temperature'
        | 'qh_cutoff'
        | 'crest_count'
        | 'hydrogens'
        | 'steric_colours'
      >
    >,
  ) => request<Settings>('PUT', '/settings', fields),

  uploadImport: (file: Blob, name: string, nodeId?: string | null) => {
    const query = new URLSearchParams({ filename: name })
    if (nodeId) query.set('node_id', nodeId)
    return request<ImportPlan>('POST', `/imports?${query.toString()}`, file)
  },
  importFromPath: (path: string, nodeId?: string | null) =>
    request<ImportPlan>('POST', '/imports/from-path', { path, node_id: nodeId ?? null }),
  previewImport: (token: string, options: ImportOptions) =>
    request<ImportPlan>('POST', `/imports/${token}/preview`, options),
  commitImport: (token: string, options: ImportOptions) =>
    request<ImportResult>('POST', `/imports/${token}/commit`, options),
  cancelImport: (token: string) => request<void>('DELETE', `/imports/${token}`),

  calculations: (nodeId: string) => request<Calculation[]>('GET', `/nodes/${nodeId}/calculations`),
  updateCalculation: (
    id: string,
    fields: Partial<LevelFields> & { geometry_level_id?: string | null; notes?: string },
  ) => request<Calculation>('PATCH', `/calculations/${id}`, fields),
  levels: () => request<Level[]>('GET', '/levels'),
  updateSourceFile: (
    id: string,
    fields: Partial<Pick<SourceFile, 'original_name' | 'origin_device' | 'origin_path'>>,
  ) => request<SourceFile>('PATCH', `/source-files/${id}`, fields),
  openSourceFile: (id: string, reveal = false) =>
    request<void>('POST', `/source-files/${id}/open${reveal ? '?reveal=true' : ''}`),
  sourceFileUrl: (id: string) => `/api/source-files/${id}/download`,

  canvas: () => request<Canvas>('GET', '/canvas'),

  createNote: (nodeId: string, fields: NoteFields) => request<Note>('POST', `/nodes/${nodeId}/notes`, fields),
  updateNote: (id: string, fields: NoteFields) => request<Note>('PATCH', `/notes/${id}`, fields),
  deleteNote: (id: string) => request<void>('DELETE', `/notes/${id}`),
  /** A picture for a note; the server reads its type from the bytes and cleans an SVG. */
  uploadNoteImage: (file: Blob) =>
    request<{ id: string; media_type: string; size: number }>('POST', '/note-images', file),
  savePositions: (positions: Record<string, { x: number; y: number }>) =>
    request<void>('PUT', '/positions', { positions }),

  createStep: (name: string) => request<Step>('POST', '/steps', { name }),
  updateStep: (id: string, fields: Partial<Pick<Step, 'name' | 'notes'>>) =>
    request<Step>('PATCH', `/steps/${id}`, fields),
  reorderSteps: (ids: string[]) => request<Step[]>('PUT', '/steps/order', { ids }),
  deleteStep: (id: string) => request<{ unassigned_nodes: number }>('DELETE', `/steps/${id}`),

  createBranch: (fields: BranchFields) => request<Branch>('POST', '/branches', fields),
  updateBranch: (id: string, fields: BranchFields) => request<Branch>('PATCH', `/branches/${id}`, fields),
  deleteBranch: (id: string) => request<{ unassigned_nodes: number }>('DELETE', `/branches/${id}`),
  arrangeBranch: (id: string) => request<{ moved: number }>('POST', `/branches/${id}/arrange`),
  splitNode: (id: string, branches: { name: string; colour?: string }[]) =>
    request<Branch[]>('POST', `/nodes/${id}/split`, { branches }),

  createTransition: (source_id: string, target_id: string, sides?: TransitionSides) =>
    request<Transition>('POST', '/transitions', { source_id, target_id, ...sides }),
  updateTransition: (id: string, fields: Partial<Pick<Transition, 'status' | 'notes' | 'source_side' | 'target_side'>>) =>
    request<Transition>('PATCH', `/transitions/${id}`, fields),
  deleteTransition: (id: string) => request<void>('DELETE', `/transitions/${id}`),
  attachSpecies: (id: string, species_id: string, direction: SpeciesDirection, count: number) =>
    request<Transition>('PUT', `/transitions/${id}/species`, { species_id, direction, count }),
  detachSpecies: (id: string, speciesId: string) =>
    request<Transition>('DELETE', `/transitions/${id}/species/${speciesId}`),

  reconnect: (member_ids: string[], label: string, outgoing: { name: string; colour?: string } | null) =>
    request<Group>('POST', '/groups/reconnect', { member_ids, label, outgoing }),
  updateGroup: (id: string, fields: GroupFields) => request<Group>('PATCH', `/groups/${id}`, fields),
  addToGroup: (id: string, node_ids: string[]) => request<Group>('POST', `/groups/${id}/members`, { node_ids }),
  removeFromGroup: (id: string, node_id: string) => request<Group>('DELETE', `/groups/${id}/members/${node_id}`),
  groupDeletePreview: (id: string) => request<GroupDeletePreview>('GET', `/groups/${id}/delete-preview`),
  dissolveGroup: (id: string, restore_branches: boolean) =>
    request<void>('POST', `/groups/${id}/dissolve`, { restore_branches }),
  deleteGroup: (id: string) => request<unknown>('DELETE', `/groups/${id}`),

  energyOptions: () => request<EnergyOptions>('GET', '/energies/options'),
  energyView: (level: string, type: EnergyType, reference: string | null) =>
    request<EnergyView>(
      'GET',
      `/energies/view?level=${encodeURIComponent(level)}&type=${type}` +
        (reference ? `&reference=${encodeURIComponent(reference)}` : ''),
    ),
  extendPathway: (path: string[], branchId?: string | null) =>
    request<Pathway>('POST', '/pathways/extend', { path, branch_id: branchId ?? null }),
  branchPathway: (branchId: string) => request<Pathway>('GET', `/branches/${branchId}/pathway`),
  profiles: (body: ProfileRequest) => request<Profiles>('POST', '/energies/profile', body),
  energyTable: (body: ProfileRequest) => request<EnergyTable>('POST', '/energies/table', body),
  energyTableCsv: async (body: ProfileRequest): Promise<Blob> => {
    const response = await fetch('/api/energies/table.csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) throw new ApiError(response.status, null)
    return response.blob()
  },

  /** D79: the investigation as one read-only HTML file, with the drawer's pathways (A30). */
  exportSnapshot: async (
    paths: { ids: string[]; branch_id: string | null }[],
    referenceId: string | null,
  ): Promise<{ blob: Blob; name: string }> => {
    const response = await fetch('/api/snapshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, reference_id: referenceId }),
    })
    if (!response.ok) {
      let detail: unknown = null
      try {
        detail = ((await response.json()) as { detail?: unknown }).detail
      } catch {
        // not JSON
      }
      throw new ApiError(response.status, detail)
    }
    const header = response.headers.get('Content-Disposition') ?? ''
    const encoded = /filename\*=UTF-8''([^;]+)/.exec(header)
    return {
      blob: await response.blob(),
      name: encoded ? decodeURIComponent(encoded[1]) : 'investigation read-only.html',
    }
  },

  modes: (calculationId: string) => request<Modes>('GET', `/calculations/${calculationId}/modes`),
  overlay: (body: OverlayRequest) => request<Overlay>('POST', '/overlay', body),
  alignmentSets: () => request<AlignmentSet[]>('GET', '/alignment-sets'),
  createAlignmentSet: (name: string, atoms: Record<string, number[]>) =>
    request<AlignmentSet>('POST', '/alignment-sets', { name, atoms }),
  updateAlignmentSet: (id: string, fields: { name?: string; atoms?: Record<string, number[] | null> }) =>
    request<AlignmentSet>('PATCH', `/alignment-sets/${id}`, fields),
  deleteAlignmentSet: (id: string) => request<void>('DELETE', `/alignment-sets/${id}`),
  selectivities: () => request<Selectivity[]>('GET', '/selectivities'),
  createSelectivity: (fields: SelectivityFields & { name: string }) =>
    request<Selectivity>('POST', '/selectivities', fields),
  updateSelectivity: (id: string, fields: SelectivityFields) =>
    request<Selectivity>('PATCH', `/selectivities/${id}`, fields),
  deleteSelectivity: (id: string) => request<void>('DELETE', `/selectivities/${id}`),
  selectivityResult: (id: string) => request<SelectivityResult>('GET', `/selectivities/${id}/result`),
  turnovers: () => request<Turnover[]>('GET', '/turnovers'),
  createTurnover: (fields: TurnoverFields & { name: string }) => request<Turnover>('POST', '/turnovers', fields),
  updateTurnover: (id: string, fields: TurnoverFields) => request<Turnover>('PATCH', `/turnovers/${id}`, fields),
  deleteTurnover: (id: string) => request<void>('DELETE', `/turnovers/${id}`),
  turnoverResult: (id: string) => request<TurnoverResult>('GET', `/turnovers/${id}/result`),
  stericProfiles: () => request<StericProfile[]>('GET', '/steric-profiles'),
  createStericProfile: (fields: StericProfileFields) => request<StericProfile>('POST', '/steric-profiles', fields),
  updateStericProfile: (id: string, fields: StericProfileFields) =>
    request<StericProfile>('PATCH', `/steric-profiles/${id}`, fields),
  deleteStericProfile: (id: string) => request<void>('DELETE', `/steric-profiles/${id}`),
  computeSterics: (id: string, nodeIds: string[], maps = false) =>
    request<StericComputed[]>('POST', `/steric-profiles/${id}/compute`, { node_ids: nodeIds, maps }),
  stericDifference: (id: string, firstId: string, secondId: string) =>
    request<StericMap>('POST', `/steric-profiles/${id}/difference`, { first_id: firstId, second_id: secondId }),
  stericTableCsv: async (id: string, nodeIds: string[]): Promise<Blob> => {
    const response = await fetch(`/api/steric-profiles/${id}/table.csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_ids: nodeIds }),
    })
    if (!response.ok) throw new ApiError(response.status, null)
    return response.blob()
  },
}

export const STATUS_LABEL: Record<string, string> = Object.fromEntries(
  STATUSES.map((s) => [s.value, s.label]),
)

/** Rejected and superseded items are drawn faded (03 §3). */
export const FADED_STATUSES = new Set<string>(['rejected', 'superseded'])

/** Energies are stored in hartree; the factors come from the backend (P17). */
export function convertEnergy(hartree: number, unit: string, factors: Record<string, number>): number {
  return hartree * (factors[unit] ?? 1)
}

/** A relative energy in the display unit, with the unit's decimals (EN-9). */
export function formatDelta(hartree: number | null, settings: Settings | null): string {
  if (hartree === null) return 'n/a'
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const value = convertEnergy(hartree, unit, settings?.energy_factors ?? {})
  const text = value.toFixed(settings?.energy_decimals[unit] ?? 2)
  return /^-0\.?0*$/.test(text) ? text.slice(1) : text // no "-0.00"
}

/** "G_qh (298.15 K, 100 cm⁻¹)": G_qh is always shown with its parameters (D58). */
export function energyTypeName(type: EnergyType, temperature?: number, cutoff?: number): string {
  if (type !== 'G_qh') return type
  return temperature === undefined ? 'G_qh' : `G_qh (${temperature} K, ${cutoff} cm⁻¹)`
}

/** Geometry as stored in history entries: [[element, x, y, z], ...]. */
export type GeometryRows = [string, number, number, number][]

export function geometryToXyz(rows: GeometryRows, comment = ''): string {
  const lines = rows.map(
    ([el, x, y, z]) =>
      `${el.padEnd(2)} ${x.toFixed(8).padStart(14)} ${y.toFixed(8).padStart(14)} ${z.toFixed(8).padStart(14)}`,
  )
  return [String(rows.length), comment, ...lines].join('\n') + '\n'
}
