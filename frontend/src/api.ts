// Thin client for the local backend API (src/chembook3d/api/routes.py). All rules live in
// the backend; the UI only shows what it returns.

import type { HydrogenMode, Rotation } from './chem'
import type { StericColours } from './display'
import type { ThemeChoice } from './theme'

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
  /** D193: the sub-investigation whose canvas the node is drawn on; null (or absent in an older
   * read-only copy) is the main canvas. Its position is on that canvas. */
  canvas_id?: string | null
  pos_x: number
  pos_y: number
  /** The orientation saved from the 3D view, used by the structure-mode card. */
  view_rotation: Rotation | null
  /** D112: it has a scan path (an xTB path.xyz or xtbscan.log, a joined scan, or D200 a Gaussian
   * or ORCA scan), so it can be shown on an edge. */
  scan_path: boolean
  /** D121: the edge a scan path node is shown on, as a chip there. */
  path_edge_id: string | null
  /** D121: set to show only the chip; layout, no history. */
  on_edge_only: boolean
  /** D121: its box is off the canvas now (set so, linked, still only a scan path, no edges or
   * group of its own). */
  on_edge: boolean
  /** D160: a free species' own concentration; null follows the investigation's standard state. */
  concentration?: Concentration | null
  created_at: string
  updated_at: string
}

/** D160: what a free species' G and G_qh are taken at, on top of the 1 M standard state. */
export type Concentration =
  | { kind: '1 M' }
  | { kind: 'neat'; solvent?: string | null; molarity?: number }
  | { kind: 'molar'; molarity: number }

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
    | 'concentration'
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
  /** D121: the scan paths run on this edge. Never the edge's energy (D27, EN-3). */
  scan_paths: PathOnEdge[]
}

/** D121: a scan path shown on its edge. */
export type PathOnEdge = {
  node_id: string
  label: string
  /** The scan path whose movie the edge panel plays. */
  calculation_id: string | null
  /** Hartree: the path's top above its first kept point, at the path's own xTB level. */
  top: number | null
  /** D116, D118: the session's quality check; null when no job record says. */
  gate: 'passed' | 'missed' | null
  reached_end: boolean | null
  /** Å: the end's RMSD over the reacting atoms (D118), and over all atoms. */
  active_rmsd: number | null
  end_rmsd: number | null
  /** Its box is off the canvas; the chip stands for it. */
  on_edge: boolean
  /** D137: "xTB", or the program of a scan joined in the scan editor. */
  program: string
}

/** D121: a path that did not reach its end: the session's gate missed, or it said so. */
export const pathMissed = (p: PathOnEdge) => p.gate === 'missed' || p.reached_end === false

/** D121: "+12.4 kcal/mol" for a path's top, or "top n/a". */
export function pathTop(p: PathOnEdge, settings: Settings | null): string {
  if (p.top === null) return 'top n/a'
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const text = formatDelta(p.top, settings)
  return `top ${p.top > 0 && !text.startsWith('-') ? '+' : ''}${text} ${unit}`
}

/** D121: the chip an edge shows for its scan paths: "xTB path · top +12.4 kcal/mol", or for
 * several "xTB paths (2) · lowest top +9.8 kcal/mol"; a joined scan names its program (D137),
 * and paths of several programs are "Scan paths (2)". */
export function pathChip(paths: PathOnEdge[], settings: Settings | null): { text: string; title: string; missed: boolean } {
  const tops = paths.filter((p) => p.top !== null)
  const lowest = tops.length ? tops.reduce((a, b) => ((b.top as number) < (a.top as number) ? b : a)) : null
  const top = lowest ? pathTop(lowest, settings) : 'top n/a'
  const programs = [...new Set(paths.map((p) => p.program || 'xTB'))]
  const name = programs.length === 1 ? `${programs[0]} path` : 'Scan path'
  const text = paths.length === 1 ? `${name} · ${top}` : `${name}s (${paths.length}) · lowest ${top}`
  const lines = paths.map(
    (p) =>
      `${p.label || 'Untitled path'}: ${pathTop(p, settings)}${pathMissed(p) ? ', did not reach end' : ''}${p.on_edge ? ' (shown on this edge only)' : ''}`,
  )
  lines.push(
    programs.every((p) => p === 'xTB')
      ? 'GFN2-xTB scan path, measured from its first kept point; not the edge’s barrier. Click to open.'
      : 'Scan path, measured from its first kept (for a scan, first converged) point at its own level; not the edge’s barrier. Click to open.',
  )
  return { text, title: lines.join('\n'), missed: paths.some(pathMissed) }
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
    .map(
      (s) =>
        `${s.count > 0 ? '+' : '−'} ${Math.abs(s.count) > 1 ? `${Math.abs(s.count)} × ` : ''}${s.label}${s.concentration ? ` (${s.concentration})` : ''}`,
    )
    .join(' ')
}

/** D160: `concentration` names a species' own concentration ("neat THF, 12.3 M"), if any. */
export type SpeciesCount = { species_id: string; label: string; count: number; concentration?: string | null }

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
  /** D193: the sub-investigation it and its members are on; null is the main canvas. */
  canvas_id?: string | null
  pos_x: number
  pos_y: number
  /** How the members are laid out when the group is expanded (A21). */
  layout: GroupLayout
  /** D197: its members' cards show their Boltzmann population %. */
  show_populations?: boolean
}

/** D193: a box on a canvas holding a canvas of its own, for a side mechanism. It has no energy
 * and no edge ends at it; edges to the nodes inside are drawn to it. */
export type SubInvestigation = {
  id: string
  label: string
  notes: string
  /** The canvas the box is on: another sub-investigation, or null for the main canvas. */
  parent_id: string | null
  pos_x: number
  pos_y: number
  /** The node inside whose structure the closed box shows, only as a picture. */
  face_id: string | null
  /** The boxes from the main canvas down to this one, outermost first. */
  path: string[]
  /** What it holds at any depth. */
  node_count: number
  ts_count: number
  group_count: number
  sub_count: number
}

export type SubInvestigationFields = Partial<Pick<SubInvestigation, 'label' | 'notes' | 'face_id' | 'pos_x' | 'pos_y'>>

export type SubDeletePreview = {
  nodes: number
  transition_states: number
  groups: number
  sub_investigations: number
  calculations: number
  transitions: number
}

export type GroupLayout = 'grid' | 'vertical' | 'horizontal'

export type GroupFields = Partial<
  Pick<Group, 'label' | 'notes' | 'step_id' | 'representative_id' | 'outgoing_branch_id' | 'pos_x' | 'pos_y' | 'layout' | 'show_populations'>
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
  /** D193: boxes holding canvases of their own; absent in a read-only copy made before them. */
  sub_investigations?: SubInvestigation[]
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

/** D101: one structure of an optimization or scan, turned onto the calculation's final geometry;
 * `stage` is the stage of an xTB scan path that names its stages (D112). */
export type StepFrame = {
  geometry: GeometryRows
  energy: number | null
  point: number | null
  converged: boolean
  stage: number | null
  /** D117: left out of the scan path by hand. */
  removed: boolean
  /** D123b: the convergence criteria the optimizer printed for this structure. */
  criteria: Criterion[]
}
/** D123b: one convergence criterion; `threshold` null for one only reported (predicted ΔE). */
export type Criterion = { name: string; value: number; threshold: number | null; converged: boolean | null; unit: string }
/** D117: where removed points leave a gap between two kept frames (0-based frame indices). */
export type TrimCut = { before: number; after: number; removed: number; jump: number | null; large: boolean }
/** D117: a scan path over its kept points. `top` is the frame the node takes, `shown` the frame
 * the calculation stands for; `automatic` when they are the same, so a trim moves it along. */
export type Trim = {
  removed: number[]
  kept: number
  top: number
  shown: number | null
  automatic: boolean
  barrier: number | null
  cuts: TrimCut[]
  spikes: number[]
  first_removed: boolean
  last_removed: boolean
}
/** `in_place`: "Use this structure" changes the node itself (a scan path node), not a derived one (D112). */
export type Steps = {
  scan: 'relaxed' | 'rigid' | null
  points: number
  frames: StepFrame[]
  in_place: boolean
  trim: Trim | null
}

/** D131: an IRC direction as the program labels it; "forward" follows the imaginary mode as
 * printed, whose sign is arbitrary. */
export type IrcDirection = 'forward' | 'reverse'
/** One point of a TS node's IRC path; `coordinate` is the app's mass-weighted distance along
 * the path from the TS in amu½·bohr, negative on the reverse side. */
export type IrcPoint = {
  side: 'ts' | IrcDirection
  number: number
  coordinate: number
  program_coordinate: number | null
  energy: number | null
  /** Hartree, to the TS point of the same file. */
  relative: number | null
  rows: GeometryRows
  calculation_id: string
  file: string
}
/** One imported file of a node's IRC. */
export type IrcPart = {
  source_file_id: string | null
  file: string
  part: 'path' | 'end'
  direction: IrcDirection | 'both'
  calculation_ids: string[]
  types: string[]
  program: string
  level: string
  termination: string
  imported_at: string
  /** False: a later IRC in the same direction started again from the TS and replaced it. */
  used: boolean
  note: string
  /** How each direction ended: minimum, max_points, complete or stopped. */
  endings: Partial<Record<IrcDirection, string>>
  max_points: number | null
  points: Partial<Record<IrcDirection, number>>
}
export type IrcValue = { value: number | null; relative: number | null; message: string | null }
export type IrcEndPoint = {
  direction: IrcDirection
  optimization_id: string | null
  frequency_id: string | null
  level: string
  converged: boolean | null
  imaginary: number | null
  /** Å: the optimization's first structure to the IRC's last point. */
  start_rmsd: number | null
  values: Record<'E' | 'G' | 'G_qh', IrcValue>
}
export type IrcSide = {
  direction: IrcDirection
  ending: string | null
  max_points: number | null
  points: number
  last_relative: number | null
  end_point: IrcEndPoint | null
  connection: IrcConnection | null
  same_as: { node_id: string; label: string; rmsd: number }[]
}
/** A node on an edge touching the TS, compared with one end of its IRC (D132). */
export type IrcNeighbour = {
  node_id: string
  label: string
  edge: string // 'incoming' or 'outgoing'
  rmsd: number | null
  rmsd_all: number | null
  same_bonds: boolean
  note: string
}
export type IrcConnection = {
  verdict: string // 'connects', 'elsewhere' or 'no_neighbours'
  /** 'optimized end point' or 'last IRC point': the structure compared. */
  judged_on: string
  node_id: string | null
  label: string
  rmsd: number | null
  atoms: number
  neighbours: IrcNeighbour[]
}
export type Irc = {
  node_id: string
  label: string
  is_ts: boolean
  parts: IrcPart[]
  points: IrcPoint[]
  sides: IrcSide[]
  ts_energy: number | null
  ts_values: Record<string, Record<'E' | 'G' | 'G_qh', { value: number | null; message: string | null }>>
  warnings: string[]
  /** The direction drawn on the left: the one leading to the TS's incoming edge (Q39). */
  left: IrcDirection
}
export type IrcSummary = {
  is_ts: boolean
  count: number
  paths: Record<IrcDirection, number>
  optimizations: Record<IrcDirection, number>
  frequencies: Record<IrcDirection, number>
}

/** D123c: a file read for the Inspect window: on this computer (`server` null) or on a server. */
export type InspectTarget = { path: string; server?: string | null }
/** One job step of an inspected file; `job_type` "structures" for an xyz file. */
export type InspectStep = {
  index: number
  job_type: string
  route: string
  method: string | null
  basis: string | null
  charge: number | null
  multiplicity: number | null
  termination: 'normal' | 'abnormal'
  energy: number | null
  optimization_converged: boolean | null
  frequencies: number[]
  imaginary: number
  scan: 'relaxed' | 'rigid' | null
  points: number
  frames: StepFrame[]
  modes: Modes | null
  missing: string[]
}
export type Inspection = {
  id: string
  name: string
  /** Where the file is, as an import records it (saga:/cluster/… on a server). */
  path: string
  server: string | null
  server_name: string | null
  size: number
  /** When it was read (seconds since 1970). */
  read_at: number
  program: string
  version: string | null
  warnings: string[]
  steps: InspectStep[]
}

/** D135: a file for the scan editor: on this computer, on a logged-in server, or an imported
 * scan's stored copy (`calculation`). */
export type ScanEditorTarget = { path?: string; server?: string | null; calculation?: string | null }
export type ScanPoint = {
  geometry: GeometryRows
  energy: number | null
  /** The scanned coordinate on this structure (Å or degrees). */
  value: number
  number: number
  structure: number
  /** A joined scan read back: where the point came from. */
  source: string
}
export type EditorScan = {
  key: string
  file: string
  path: string
  server_name: string | null
  step: number
  steps: number
  program: string
  version: string | null
  level: string
  basis_named: boolean
  /** D167: the scans the chart draws against one energy zero share it. */
  energy_group: string
  charge: number | null
  multiplicity: number | null
  atoms: number[]
  coordinate: string
  unit: string
  /** "file" when the file names the coordinate, "structures" when it was found from them. */
  found_by: 'file' | 'structures'
  left_out: number
  ended: boolean
  joined: boolean
  /** D136: the file runs the scan again after it; only its first run is read. */
  restarted: boolean
  /** D136: whose atom order its points were put in, when they were. */
  renumbered: string
  points: ScanPoint[]
}
/** D136: two branches of one coordinate whose energies cross; `a` is lower before `x`. */
export type ScanCrossing = { a: string; b: string; x: number; rmsd: number | null; text: string }
export type ScanEditorState = {
  id: string
  scans: EditorScan[]
  /** The scans the files just added hold. */
  added: string[]
  problems: { path: string; problem: string }[]
  crossings: ScanCrossing[]
  /** What matching atoms did. */
  note?: string | null
  /** D138: after a refresh, the path's pieces moved onto the points read again. */
  pieces?: ScanPiece[] | null
}
/** D136: a finer scan that fits inside the path. */
export type ScanOffer = { scan: string; points: number; replaced: number; low: number; high: number; text: string }
/** D136: a scan's point fitted onto the first compared. */
export type ComparedPoint = {
  scan: string
  index: number
  geometry: GeometryRows
  energy: number | null
  value: number
  rmsd: number | null
}
export type JoinRequest = {
  pieces: ScanPiece[]
  add?: string | null
  chain?: string[] | null
  replace?: string | null
  between?: [number, number] | null
  crossing?: ScanCrossing | null
}
/** A run of one scan's points, from `first` to `last` (reversed when `last` is before it). */
export type ScanPiece = { scan: string; first: number; last: number }
export type JoinedFrame = {
  geometry: GeometryRows
  energy: number | null
  x: number
  piece: number
  scan: string
  index: number
  number: number
  structure: number
  source: string
}
export type JoinMark = {
  at: number
  kind: 'join' | 'shared' | 'energies' | 'jump' | 'step' | 'back' | 'closed'
  text: string
  warning: boolean
}
export type JoinedPath = {
  pieces: ScanPiece[]
  /** What adding or chaining scans did. */
  notes: string[]
  frames: JoinedFrame[]
  axis: 'coordinate' | 'path'
  atoms: number[]
  unit: string
  label: string
  joins: {
    after: number
    at: number
    shared: boolean
    rmsd: number | null
    rmsd_active: number | null
    energy_difference: number | null
    ok: boolean
  }[]
  marks: JoinMark[]
  top: number | null
  /** kcal/mol, the top above the first frame. */
  barrier: number | null
  level: string
  level_notes: string[]
  /** Why these pieces cannot be joined (another level, other atoms). */
  blocked: string | null
  /** The scan whose atoms are numbered in another order, to be matched. */
  mismatch: string | null
  offers: ScanOffer[]
  /** The file Save writes. */
  text: string
}

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

/** D113: the end's atoms matched to the start's numbering. Atom numbers are 1-based, of the
 * full structure; bonds and centres are in the start's numbering. */
export type AtomMatch = {
  start_id: string
  end_id: string
  /** For each start atom in order, the end's atom matched to it. */
  mapping: number[]
  rmsd: number
  formed: [number, number][]
  broken: [number, number][]
  inverted: number[]
  /** [start atom, end atom] pairs fixed by hand. */
  fixed: [number, number][]
  same_numbering: boolean
  confident: boolean
  doubts: string[]
  start_xyz: string
  end_xyz: string
  /** The end in the start's order, placed on the start. */
  renumbered_xyz: string
}

/** D114: one end of a scan path; a group end stands for one of its members. */
export type ScanPathEnd = {
  node_id: string
  label: string
  role: Role
  group_id: string | null
  group_label: string | null
  members: { id: string; label: string }[]
}

export type ScanPathCoordinate = {
  kind: 'distance' | 'angle' | 'dihedral'
  /** 1-based, in the start's numbering. */
  atoms: number[]
  ticked: boolean
  why: string
  start_value: number
  end_value: number
}

export type ScanPathTsEnd = {
  end: 'start' | 'end'
  node_id: string
  label: string
  /** cm⁻¹, negative; null for a guess. */
  imaginary: number | null
  guess: boolean
  frequency_job: boolean
  suggested: ScanPathCoordinate[]
}

/** D120: a free species joining or leaving along a scan path, in the start's numbering. */
export type ScanPathSpecies = {
  node_id: string
  label: string
  direction: 'joins' | 'leaves'
  /** The end where it is bound. */
  bound: 'start' | 'end'
  atoms: number[]
  bonds: [number, number][]
  anchor: number[]
  clearance: number
  pull: number
  closest: number
  separated_xyz: string
  bound_xyz: string
}

export type ScanPathPlan = {
  start: ScanPathEnd
  end: ScanPathEnd
  edge_id: string
  match: AtomMatch
  charge: number
  multiplicity: number
  solvent: string | null
  solvents: string[]
  ts_ends: ScanPathTsEnd[]
  /** D116: e.g. no bond forms or breaks between the ends. */
  warnings: string[]
  species: ScanPathSpecies | null
}

export type ScanPathRequest = {
  start_id: string
  end_id: string
  pairs?: [number, number][]
  start_member_id?: string | null
  end_member_id?: string | null
  /** D120: the separated species' closest contact with the complex, Å. */
  clearance?: number
}

/** D93: a calculation job handed to a Claude Code cloud session, as the app records it. */
export type CloudJob = {
  id: string
  name: string
  created: string
  status: 'draft' | 'starting' | 'waiting_for_answer' | 'launch_failed' | 'running' | 'finished' | 'fetched'
  session_url: string | null
  launch_error: string | null
  warning: string | null
  fetch_error: string | null
  result: { status?: string; summary?: string } | null
  result_branch?: string | null
  /** the launch's screen while it waits for an answer in the Claude panel */
  question?: string | null
  kind?: string
  scan_path?: { start_id: string; end_id: string; start_label: string; end_label: string }
  /** D115: the node a scan path's result was imported as */
  imported?: { node_id: string; at: string; present?: boolean } | null
  /** D115: why the path could not be imported; the app does not try again by itself */
  import_error?: string | null
}

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
  // undo_import (one file) and undo (a batch) undo an import (D102)
  action:
    | 'create'
    | 'update'
    | 'delete'
    | 'split'
    | 'reconnect'
    | 'add_members'
    | 'remove_member'
    | 'dissolve'
    | 'undo_import'
    | 'undo'
  field: string | null
  old_value: unknown
  new_value: unknown
  source: string
  /** D102: the entry's import can still be undone, one file or a whole batch */
  undo?: 'import' | 'batch' | null
}

/** D102: what undoing one imported file removes and puts back. */
export type UndoFile = {
  source_file_id: string
  file: string
  blockers: string[]
  calculations: { type: string; level: string; step: number; node_id: string }[]
  deleted: { id: string; label: string }[]
  group: { id: string; label: string } | null
  restored: { node_id: string; label: string; field: string }[]
  kept: { node_id: string; label: string; field: string; value: unknown }[]
  tags: { node_id: string; label: string; removed: string[]; added: string[] }[]
}

export type UndoPreview = {
  kind: 'import' | 'batch'
  folder: string | null
  blockers: string[]
  files: UndoFile[]
}

export type RecentInfo = {
  name: string | null
  opened: string | null
  nodes: number | null
  branches: number | null
}

export type Settings = {
  energy_unit: string
  energy_units: string[]
  energy_factors: Record<string, number>
  recent: string[]
  /** D182: name, last opened (UTC) and counts of each recent folder, by its path in `recent`, as
   * of its last open or close; a field is missing for a folder an older version opened. */
  recent_info: Record<string, RecentInfo>
  last_device: string
  /** D78: the folder a file was last picked from in the import dialog ("" when none or gone). */
  last_import_folder: string
  geometry_tolerance: number
  duplicate_tolerance: number
  energy_decimals: Record<string, number>
  /** D159: the open investigation's G_qh temperature and cutoff and standard state
   * (`thermo_scope` "investigation"), or with none open the defaults for new ones ("app"). */
  qh_temperature: number
  qh_cutoff: number
  /** D95: the standard state of G and G_qh; "1 M" adds RT ln(V_m / 1 L mol⁻¹) per molecule. */
  standard_state: StandardState
  standard_states: StandardState[]
  thermo_scope: 'investigation' | 'app'
  /** D159: what a new investigation starts with (the app's settings). */
  thermo_defaults: { qh_temperature: number; qh_cutoff: number; standard_state: StandardState }
  /** D160: the neat solvents a free species' concentration offers, in mol/L at 20 °C. */
  solvents?: { name: string; molarity: number }[]
  /** D34: how many of the lowest CREST conformers are ticked on import. */
  crest_count: number
  /** Hydrogens drawn in the 3D views and on structure cards. */
  hydrogens: HydrogenMode
  hydrogen_modes: HydrogenMode[]
  /** D84: the colours of the steric maps. */
  steric_colours: StericColours
  /** D185: the app's colours: light, dark or as the system prefers. */
  theme: ThemeChoice
  themes: ThemeChoice[]
  /** D106: how energy profiles are drawn, and the presets the style dialog offers. */
  profile_style: ProfileStyle
  profile_presets: Record<'screen' | 'publication', ProfileStyle>
}

/** D106: how an energy profile is drawn, in the app and in the PNG and SVG saved from it. The
 * backend keeps each value within its choices and bounds (`settings.clean_profile_style`). */
export type ProfileStyle = {
  width: number
  height: number
  font: 'system' | 'arial' | 'helvetica' | 'times'
  font_size: number
  colours: 'branch' | 'colour-blind' | 'grey' | 'black'
  text: 'soft' | 'black'
  level_width: number
  level_thickness: number
  connector: 'straight' | 'curved'
  dash: 'solid' | 'dashed' | 'dotted' | 'by-pathway'
  connector_width: number
  value_position: 'above' | 'below' | 'hidden'
  name_position: 'below' | 'above' | 'bottom' | 'hidden'
  brackets: 'none' | 'round' | 'square'
  decimals: 'unit' | '0' | '1' | '2' | '3'
  title: boolean
  legend: 'top-right' | 'top-left' | 'hidden'
  grid: boolean
  y_axis: boolean
  step_names: boolean
  /** D108: the "no TS" tag on a direct connection's connector. */
  edge_tags: boolean
  background: 'white' | 'none'
  png_scale: number
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

/** D94: a saved custom basis set (FR-CALC-03), as listed. */
export type CustomBasisSummary = {
  id: string
  name: string
  description: string
  elements: string[]
  created_at: string
  calculation_count: number
}

/** D94: one element's definition; an element has several when its atoms differed. */
export type BasisVariant = {
  header: string
  contraction: string
  shells: { kind: string; scale: string; primitives: string[][] }[]
  ecp: {
    core_electrons: number | null
    max_angular: number
    blocks: { title: string; terms: string[][] }[]
  } | null
  /** The element's lines in Gaussian Gen/GenECP input. */
  text: string[]
}

export type CustomBasisDetail = Omit<CustomBasisSummary, 'elements' | 'calculation_count'> & {
  elements: { element: string; variants: BasisVariant[] }[]
  calculations: { id: string; type: string; node_id: string; node_label: string; geometry_level_only: boolean }[]
}

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
  /** D168: a later free optimization on the node started where this one ended (absent in a
   * read-only copy made before it). */
  pre_optimization?: boolean
  warnings: Finding[]
  source_file: SourceFile | null
  result: CalculationResult | null
  quasi_harmonic: QuasiHarmonic | null
  /** D162: the ⟨S²⟩ printed (absent in a read-only copy made before it). */
  spin?: Spin | null
  /** D163: the printed G's model and the frequency scale factor (absent in a read-only copy
   * made before it; null for a calculation without frequencies). */
  thermo?: ThermoModel | null
  notes: string
  /** D117: scan points left out of a scan path. */
  removed_points: number[]
  created_at: string
}

/** D162: the spin expectation value a calculation printed (Gaussian's before annihilation,
 * with `after` the value after it), S(S+1) of its multiplicity, and whether it is further from
 * it than 10 % (a singlet: above 0.1), which is W-SPIN. */
export type ThermoModel = {
  program: string
  /** RRHO (Gaussian), qRRHO (ORCA, Grimme's entropy), mRRHO (xTB); null for another program. */
  model: string | null
  model_text: string | null
  /** The scale factor the program's own thermochemistry used (1 when the file names none). */
  scale: number
  /** The printed frequencies already carry it (ORCA), so G_qh is computed from them scaled. */
  frequencies_scaled: boolean
  qh_scale: number
}

/** D163: a scale factor as few decimals as it needs: 0.97, 1. */
export function scaleText(scale: number): string {
  return String(Number(scale.toFixed(4)))
}

/** D163: what "G" is at a level or in a profile, from the printed G's models there ("Gaussian's
 * RRHO"): the badge text (the models alone) and its tooltip; null when there is none. */
export function gModelBadge(models: string[] | undefined): { text: string; title: string } | null {
  if (!models || models.length === 0) return null
  const short = models.map((m) => m.split("'s ").pop() ?? m)
  const mixed = models.length > 1
  return {
    text: mixed ? `${short.join(' + ')} ⚠` : short[0],
    title:
      `G is each frequency job's printed G: ${models.join(', ')}. ` +
      'Gaussian prints pure RRHO, ORCA Grimme\'s quasi-RRHO entropy, xTB its modified RRHO' +
      (mixed ? '; this level mixes them (W-G-MODEL).' : '.') +
      ' G_qh is computed the same way for every program.',
  }
}

/** D162: the spin expectation value a calculation printed (Gaussian's before annihilation,
 * with `after` the value after it), S(S+1) of its multiplicity, and whether it is further from
 * it than 10 % (a singlet: above 0.1), which is W-SPIN. */
export type Spin = {
  s2: number
  after: number | null
  ideal: number | null
  multiplicity: number | null
  contaminated: boolean
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
  /** D103: a node or group; each TS is then balanced by the free species from it (D72). */
  reference_id: string | null
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
  /** D103: the free species balancing this TS from the reference, and their energy. */
  species: SpeciesCount[]
  species_value: number | null
  message: string | null
  /** D154: what the frequency job at the level says against this TS, if anything. */
  check?: EnergyCheck | null
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
  standard_state: StandardState
  /** D160: the temperatures of the frequency jobs H and G are read from. */
  job_temperatures?: number[]
  conformers: 'boltzmann' | 'lowest'
  reference_id: string | null
  reference_label: string | null
  outcomes: SelectivityOutcomeResult[]
  excess: { label: 'ee' | 'de'; boltzmann: Excess; lowest: Excess; experimental: Excess } | null
  notes: string[]
}

/** D171: a saved conformer equilibrium: populations of conformers, the rates of the TSs
 * between them and the time the set takes to reach equilibrium. */
export type EquilibriumMember = {
  /** A node or group; TS nodes are the barriers, a group counts with its members. */
  id: string
  /** The number of equivalent structures it stands for (D171d). */
  count: number
}

export type Equilibrium = {
  id: string
  name: string
  members: EquilibriumMember[]
  level: string | null
  energy_type: EnergyType
  /** K; null follows the G_qh temperature setting. */
  temperature: number | null
  /** The conformer the populations start from over time; null: the slowest start. */
  start_id: string | null
  /** D173: TS nodes and groups of the step that follows, for the Curtin–Hammett check. */
  next_ids: string[]
  notes: string
}

export type EquilibriumFields = Partial<Omit<Equilibrium, 'id'>>

export type EquilibriumConformer = {
  id: string
  label: string
  count: number
  /** Hartree; `relative` from the lowest conformer. */
  value: number
  relative: number
  /** Percent of all conformers. */
  population: number
  /** Index into `sets`. */
  set: number
  /** D171g: the highest point on the lowest way from `barrier_from_id`, hartree. */
  effective_barrier: number | null
  barrier_from_id: string | null
}

export type EquilibriumDirection = {
  from_id: string
  to_id: string
  /** Hartree; rate in 1/s, half-life in s. */
  barrier: number
  rate: number
  half_life: number
}

export type EquilibriumBarrier = {
  id: string
  label: string
  count: number
  value: number
  relative: number
  /** The conformers it joins (two), or those it has edges to when it joins none. */
  joins: string[]
  directions: EquilibriumDirection[]
}

export type EquilibriumSet = {
  conformer_ids: string[]
  start_id: string | null
  start_chosen: boolean
  /** Seconds, fastest first; `slowest` is the largest. */
  relaxation_times: number[]
  unresolved: number
  slowest: number | null
  /** Seconds until every population is within 1 percentage point of equilibrium. */
  equilibrium_time: number | null
  /** Times in s; populations (0–1, within the set) per conformer, in `conformer_ids` order. */
  curve: { times: number[]; populations: number[][] } | null
}

export type EquilibriumNextMember = {
  id: string
  label: string
  /** Hartree, with its free species; `barrier` from the lowest conformer. */
  value: number | null
  species: { species_id: string; label: string; count: number }[]
  barrier: number | null
  /** Why it is left out. */
  message: string | null
}

/** D173: the next step from the pool of the lowest conformer's set (Curtin–Hammett). */
export type EquilibriumNextStep = {
  members: EquilibriumNextMember[]
  /** Hartree; G of the pool from the lowest conformer. */
  pool: number | null
  effective_barrier: number | null
  /** 1/s, and its time 1/rate in s. */
  rate: number | null
  time: number | null
  /** The slowest relaxation time of the pool, s, and its highest effective barrier. */
  interconversion_time: number | null
  interconversion_barrier: number | null
  /** time / interconversion_time, and RT ln of it (hartree). */
  ratio: number | null
  ddg: number | null
  faster: 'interconversion' | 'next step' | null
  message: string | null
  notes: string[]
}

export type EquilibriumResult = {
  status: 'ok' | 'n/a' | 'refused' | 'incomplete'
  message: string | null
  level: string | null
  level_label: string | null
  energy_type: EnergyType
  temperature: number
  temperature_from_settings: boolean
  cutoff: number
  standard_state: StandardState
  job_temperatures?: number[]
  unit: string
  lowest_id: string | null
  start_id: string | null
  conformers: EquilibriumConformer[]
  barriers: EquilibriumBarrier[]
  sets: EquilibriumSet[]
  next_step: EquilibriumNextStep | null
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
  standard_state: StandardState
  /** D160: the temperatures of the frequency jobs H and G are read from. */
  job_temperatures?: number[]
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
  /** D161: `bsse` when a node has a counterpoise job's BSSE at the level. D160: `job_temperatures`,
   * those of the frequency jobs H and G at the level are read at. D163: `g_models`, the printed
   * G's models at the level ("Gaussian's RRHO"). */
  levels: {
    key: string
    label: string
    types: EnergyType[]
    bsse?: boolean
    job_temperatures?: number[]
    g_models?: string[]
  }[]
  temperature: number
  cutoff: number
  standard_state: StandardState
}

/** One node's or group's energy in hartree, or null with the reason (EN-3: no fallback). */
export type EnergyValue = {
  value: number | null
  code: string | null
  message: string | null
  energy_calculation_id: string | null
  thermo_calculation_id: string | null
  details: Record<string, unknown>
  /** D154: what the frequency job at the geometry level says against the node's role. */
  check?: EnergyCheck | null
}

/** D154: a transition state without exactly one imaginary frequency (or without a frequency
 * job), or a minimum with one, at the level shown; D158: also a frequency job not at a
 * stationary point (W-FREQ-NONSTAT), an unfinished optimization (W-OPT-INC) or a calculation
 * that ended abnormally (W-TERM). `code` and `message` are the first issue and every message
 * joined; `issues` lists each. Never changes the value or a status (D19). */
export type EnergyCheck = { code: string; message: string; issues?: { code: string; message: string }[] }

/** D160: W-TEMP (H or G values read at different temperatures, or at one other than G_qh's) and
 * W-STDSTATE (free species joining or leaving at 1 atm in a solvation model). */
export type EnergyNote = { code: string; message: string }

export type EnergyView = {
  level: string
  type: EnergyType
  /** D161: each node's counterpoise BSSE at the level was added ("With BSSE"). */
  bsse?: boolean
  values: Record<string, EnergyValue>
  edges: Record<string, { delta: number | null; direct: boolean; message?: string | null }>
  reference_id: string | null
  /** ΔX of each node and group from the reference, with the species that balance it (D72). */
  relative: Record<string, { value: number | null; species: SpeciesCount[]; message: string | null; joined: boolean }>
  notes?: EnergyNote[]
  /** D197: by group id, for the groups that show their members' populations. */
  populations?: Record<string, GroupPopulation>
}

/** D197: a group's members' Boltzmann shares at the view's level and type. */
export type GroupPopulation = {
  status: 'ok' | 'incomplete' | 'refused'
  message: string | null
  /** Transition states share the flux over them, not a population. */
  kind: 'conformers' | 'transition_states'
  type: EnergyType
  temperature: number
  counted: number
  total: number
  /** Percent of the members counted; null with why for one not counted. */
  members: Record<string, { percent: number | null; message: string | null }>
  notes: string[]
}

export type PathChoice = {
  transition_id: string
  node_id: string
  label: string
  status: string
  /** D108: a node or a group; a node's group, when it is in one. */
  kind?: 'node' | 'group'
  group_label?: string | null
}
export type Pathway = { path: string[]; choices: PathChoice[]; closed: boolean }

/** D190: a pathway of a saved energy profile; `shown` draws it on the figure. */
/** D194: the step at index `at` of a pathway, and every step after it, moved `by` columns later. */
export type PathShift = { at: number; by: number }
export type ProfilePath = {
  ids: string[]
  branch_id: string | null
  shown: boolean
  shifts?: PathShift[]
  /** D195: the colour the user chose ("#rrggbb"); null: the branch's colour. */
  colour?: string | null
}
/** D190: a saved energy profile, a named figure of pathways the drawer opens. */
export type EnergyProfile = { id: string; name: string; paths: ProfilePath[] }

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
  /** D181: a TS point's ΔX‡ of its step in hartree (D177), and the index of the point on the
   * pathway it is measured from; absent from copies made before D181. */
  step_barrier?: number | null
  step_barrier_from?: number | null
}

export type Profiles = {
  level: string
  level_label: string
  type: EnergyType
  /** D161: each node's counterpoise BSSE at the level was added. */
  bsse?: boolean
  reference_id: string
  reference_value: number | null
  temperature: number
  cutoff: number
  standard_state: StandardState
  /** D160: the temperatures of the frequency jobs H and G are read from, and the view's notes. */
  job_temperatures?: number[]
  notes?: EnergyNote[]
  /** D163: the printed G's models behind the points ("Gaussian's RRHO"). */
  g_models?: string[]
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
  /** D161: add each node's counterpoise BSSE at the level (off by default). */
  bsse?: boolean
}

/** D177: beside a table row, its point, branch, role and a TS row's ΔX‡ of the step (not in the CSV). */
export type EnergyTableRow = { id: string; branch_id: string | null; is_ts: boolean; barrier: string | null }

export type EnergyTable = { columns: string[]; rows: string[][]; points?: EnergyTableRow[]; notes?: EnergyNote[] }

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
  kind: 'steps' | 'ensemble' | 'irc'
  ensemble: EnsemblePlan | null
  /** D134: an IRC file planned as an ordinary import: the TS nodes it starts from, nearest first. */
  irc_targets?: { node_id: string; label: string; rmsd: number }[] | null
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
  /** D131: import the file as part of the target TS node's IRC: its path, or a job run on one
   * of its end points ('forward' or 'reverse'). */
  irc_part?: 'path' | 'end' | null
  irc_direction?: IrcDirection | null
  /** D137: a scan path (a joined scan) shown on this edge, its box on the canvas or not. */
  path_edge_id?: string | null
  on_edge_only?: boolean
  /** D193: the canvas a new node goes on (the one open); null for the main canvas. */
  canvas_id?: string | null
}

export type ImportResult = {
  node_id: string | null
  derived_node_id: string | null
  calculation_ids: string[]
  group_id: string | null
}

/** D97: where a file of a batch goes: an existing node, the node another file of the batch
 * makes, or a new node or group. */
export type BatchTarget = {
  kind: 'node' | 'file' | 'new' | 'group'
  node_id: string | null
  file_id: string | null
  label: string
  file?: string
}

export type BatchRow = {
  id: string
  path: string
  name: string
  program: string
  version: string | null
  size: number
  kind: 'steps' | 'ensemble' | 'irc'
  included: boolean
  skipped: 'imported_before' | 'same_file' | null
  skipped_detail: string | null
  order: number | null
  match: 'geometry' | 'first_geometry' | 'name' | 'name_close' | 'new' | 'chosen' | 'duplicate' | 'none' | 'irc'
  target: BatchTarget | null
  mode: 'new' | 'planned' | 'onto' | null
  derived: boolean
  candidates: { node_id: string; label: string }[]
  duplicates: (BatchTarget & { rmsd: number })[]
  duplicate_action: 'attach' | 'new' | null
  label: string | null
  jobs: string
  level_label: string | null
  termination: 'normal' | 'abnormal'
  atom_count: number
  steps: Pick<
    StepPlan,
    'index' | 'type' | 'level_label' | 'geometry_level_label' | 'termination' | 'optimization_converged' | 'energy' | 'assignment'
  >[]
  warnings: Finding[]
  blockers: string[]
  /** D104: a step's method needs a basis set the file never names (ChkBasis) */
  basis_missing: boolean
  /** the basis set it gets at import instead */
  basis_given: string | null
}

/** D97, FR-IMP-14: the proposed import of a whole folder; nothing is stored until Import. */
export type BatchPlan = {
  token: string
  folder: string
  recursive: boolean
  suffixes: string[]
  origin_device: string
  rows: BatchRow[]
  other_count: number
  unreadable: { path: string; reason: string }[]
  names: NameRequest[]
  blockers: string[]
  counts: {
    files: number
    included: number
    new: number
    attached: number
    finished: number
    skipped: number
    basis_missing: number
  }
  /** D104: the basis set given for every file that names none */
  missing_basis: string
}

export type BatchRowChoice = {
  included?: boolean | null
  /** "auto", "new", "node:<id>" or "file:<file id>" */
  target?: string | null
  duplicate_action?: 'attach' | 'new' | null
  label?: string | null
  /** D104: this file's basis set if it names none; overrides the batch's */
  basis?: string | null
}

export type BatchOptions = {
  rows?: Record<string, BatchRowChoice>
  basis_names?: Record<string, string>
  dispersion_names?: Record<string, string>
  /** D104: the basis set for every file that names none */
  missing_basis?: string | null
  origin_device?: string | null
  suffixes?: string[] | null
  /** D193: the canvas new nodes go on (the one open); null for the main canvas. */
  canvas_id?: string | null
}

export type BatchImported = {
  imported: number
  files: { file: string; how: string; node_id: string | null; group_id: string | null; label: string; calculations: number }[]
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

/** Whether the Claude panel can run here (D92). */
export type ClaudeStatus = {
  /** Claude Code was found on this computer. */
  available: boolean
  /** The panel may be used from this page (the app listens on 127.0.0.1 only). */
  enabled: boolean
  reason: string | null
  /** The chembook3d MCP server (D91) is part of this version of the app. */
  notebook_tools: boolean
  install: { command: string; docs: string }
}

/** D91: this tab's name in the backend's change counter, so it reloads only for changes made
 * elsewhere (by Claude, or in another tab). */
export const CLIENT_ID = `tab-${Math.random().toString(36).slice(2, 10)}`

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const raw = body instanceof Blob
  const headers: Record<string, string> = { 'X-Chembook-Client': CLIENT_ID }
  if (body !== undefined) headers['Content-Type'] = raw ? 'application/octet-stream' : 'application/json'
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
    signal,
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

/** D91: a delete Claude asks for; the app does it only on the user's Confirm. */
export type ClaudeRequest = {
  id: string
  action: string
  summary: string
  reason: string
  status: 'pending' | 'running' | 'done' | 'refused' | 'expired' | 'failed'
  seconds_left: number
  error: string | null
}

export type Live = {
  version: number
  /** Something was changed elsewhere since the version asked about. */
  changed: boolean
  confirm_version: number
  confirmations: ClaudeRequest[]
  investigation: string | null
}

/** What the tab shows, for Claude's get_selection (D91). */
export type SelectionReport = {
  nodes: string[]
  groups: string[]
  transitions: string[]
  branch_id: string | null
  view: string
  level: string | null
  energy_type: string | null
  reference_id: string | null
}

/** D105: what the notebook showed when last used, saved in the investigation (view only, not in
 * the history). The backend drops records deleted since. */
export type ViewState = {
  level: string | null
  energy_type: EnergyType | null
  reference_id: string | null
  edge_energies: boolean
  filters: { branches: string[]; statuses: string[]; steps: string[] }
  /** Expanded groups and (D193) sub-investigation boxes. */
  expanded: string[]
  /** D193: the sub-investigation whose canvas is open; null for the main canvas. */
  canvas?: string | null
  drawer: {
    open: boolean
    tab: 'profile' | 'table'
    /** D190: the saved energy profile the drawer has open. */
    profile_id: string | null
  }
}

/** D128: the view a read-only copy opens on, as the app showed it when the copy was made. */
export type SharedViewState = Omit<ViewState, 'reference_id' | 'drawer'> & {
  mode: 'compact' | 'energy' | 'structure' | null
  drawer: { open: boolean; tab: 'profile' | 'table' }
}

/** D107: what a tab's long poll on the backend's presence says. */
export type Presence = {
  /** Started by the launcher: the server stops when the last tab has closed. */
  launched: boolean
  stopping: boolean
  /** From the launcher: why the checkout was not updated, or the old interface is shown. */
  notices: string[]
  /** After Shut down: how the push of a synced investigation went. */
  sync: string | null
}

/** D107: what the Shut down question mentions. */
export type ShutdownInfo = { launched: boolean; investigation: boolean; linked: boolean; claude_panel: boolean }

/** A `claude --cloud` launch of a calculation job that waits for the user's answer (D93). */
export type LaunchQuestion = { job_id: string; name: string; screen: string }

/** D122: a saved SSH server such as Saga, and its connection held by the keeper. */
export type RemoteServer = {
  id: string
  name: string
  host: string
  port: number
  username: string
  connected: boolean
  /** When the login happened (seconds since 1970). */
  since: number | null
  /** The shell's current directory, when it reports it. */
  cwd: string | null
  tracked: boolean
  shell: 'running' | 'ended' | null
  /** Why the last connection ended, when it was not a log out. */
  lost: string | null
  /** D123a: the next shell forwards X11 windows to this display on this computer. */
  x11: boolean
  x11_display: string
  /** The display the running shell forwards to, and whether an X server answers there. */
  shell_x11: string | null
  x11_ready: boolean | null
  /** D123c: the shell has the `inspect FILE` command (and `scans FILE`, D138). */
  inspect: boolean
  /** D126: the user lets Claude write new files in the current directory in this login. */
  claude_writes: boolean
  /** What Claude wrote in this login, oldest first. */
  claude_written: WrittenFile[]
}

/** D126: a file Claude wrote on a server. */
export type WrittenFile = {
  path: string
  size: number
  /** Seconds since 1970. */
  time: number
  /** The original of an edited copy, unchanged. */
  based_on: string | null
  folders: string[]
}

export type RemoteStatus = { enabled: boolean; reason: string | null; servers: RemoteServer[] }

export type ServerFields = {
  id?: string
  name: string
  host: string
  port: number
  username: string
  x11?: boolean
  x11_display?: string
}

/** What is saved of a server, to save the list again. */
export function serverFields(server: RemoteServer): ServerFields {
  const { id, name, host, port, username, x11, x11_display } = server
  return { id, name, host, port, username, x11, x11_display }
}

/** The next step of a login (D122b). */
export type LoginStep = {
  kind: 'prompts' | 'host_key' | 'connected' | 'failed' | 'waiting'
  login: string | null
  name: string
  instructions: string
  banner: string
  prompts: { text: string; echo: boolean }[]
  host: string | null
  port: number | null
  algorithm: string | null
  fingerprint: string | null
  message: string | null
}

/** D122e: what is in a directory on a server, by default the terminal's current one. */
export type RemoteEntry = {
  name: string
  kind: 'file' | 'directory'
  size: number | null
  modified: number | null
  /** Named as an output the import reads (.log, .out, .output, .xyz, .cml). */
  output: boolean
}
export type RemoteListing = { server: string; path: string; entries: RemoteEntry[] }
/** D122f: where a structure went, or the file of that name already there (sent false). */
export type RemoteSent = { path: string; sent: boolean; exists: boolean }

const serverPath = (id: string) => `/remote/servers/${encodeURIComponent(id)}`
const loginPath = (id: string) => `/remote/logins/${encodeURIComponent(id)}`

export const api = {
  remoteStatus: () => request<RemoteStatus>('GET', '/remote'),
  saveServers: (servers: ServerFields[]) => request<RemoteServer[]>('PUT', '/remote/servers', { servers }),
  remoteLogin: (id: string, body: { username?: string; rows: number; cols: number }) =>
    request<LoginStep>('POST', `${serverPath(id)}/login`, body),
  remoteAnswer: (login: string, answers: string[]) =>
    request<LoginStep>('POST', `${loginPath(login)}/answer`, { answers }),
  remoteNext: (login: string) => request<LoginStep>('POST', `${loginPath(login)}/next`),
  remoteTrust: (login: string) => request<LoginStep>('POST', `${loginPath(login)}/trust`),
  remoteCancel: (login: string) => request<void>('DELETE', loginPath(login)),
  remoteLogout: (id: string) => request<void>('POST', `${serverPath(id)}/logout`),
  /** D126: let Claude write new files in the terminal's directory until log out, or stop it. */
  remoteClaudeWrites: (id: string, allow: boolean) =>
    request<void>('PUT', `${serverPath(id)}/claude-writes`, { allow }),
  /** A new shell after the last one ended, or in place of the running one (`restart`). */
  remoteShell: (id: string, rows: number, cols: number, restart = false) =>
    request<void>('POST', `${serverPath(id)}/shell`, { rows, cols, restart }),
  remoteFiles: (id: string, path?: string) =>
    request<RemoteListing>('GET', `${serverPath(id)}/files${path ? `?${new URLSearchParams({ path })}` : ''}`),
  /** Copy one output down and stage it: the import dialog's preview (D122e). */
  remoteImport: (id: string, path: string, nodeId?: string | null) =>
    request<ImportPlan>('POST', `${serverPath(id)}/import`, { paths: [path], node_id: nodeId ?? null }),
  /** Copy several outputs down and scan them as a batch import (D97, D122e). */
  remoteBatch: (id: string, paths: string[]) => request<BatchPlan>('POST', `${serverPath(id)}/batch-import`, { paths }),
  /** Write a node's coordinates as <label>.xyz into the terminal's directory (D122f). */
  remoteSend: (id: string, nodeId: string, replace = false) =>
    request<RemoteSent>('POST', `${serverPath(id)}/send`, { node_id: nodeId, replace }),
  /** D123c: read a file for the Inspect window, again on Refresh; nothing is imported. */
  inspect: (target: InspectTarget) =>
    request<Inspection>('POST', '/inspections', { path: target.path, server: target.server ?? null }),
  refreshInspection: (id: string) => request<Inspection>('POST', `/inspections/${encodeURIComponent(id)}/refresh`),
  closeInspection: (id: string) => request<void>('DELETE', `/inspections/${encodeURIComponent(id)}`),
  /** Stage the inspected file (the copy made last) for the import dialog's preview. */
  importInspected: (id: string, nodeId?: string | null) =>
    request<ImportPlan>('POST', `/inspections/${encodeURIComponent(id)}/import`, { node_id: nodeId ?? null }),
  /** D135: the scan editor: files read without importing them, joined into one path. */
  openScanEditor: (targets: ScanEditorTarget[]) => request<ScanEditorState>('POST', '/scan-editor', { targets }),
  addScanFiles: (id: string, targets: ScanEditorTarget[]) =>
    request<ScanEditorState>('POST', `/scan-editor/${encodeURIComponent(id)}/files`, { targets }),
  removeEditorScan: (id: string, key: string) =>
    request<void>('DELETE', `/scan-editor/${encodeURIComponent(id)}/scans/${encodeURIComponent(key)}`),
  joinScans: (id: string, body: JoinRequest) =>
    request<JoinedPath>('POST', `/scan-editor/${encodeURIComponent(id)}/path`, body),
  compareScanPoints: (id: string, points: { scan: string; index: number }[]) =>
    request<ComparedPoint[]>('POST', `/scan-editor/${encodeURIComponent(id)}/compare`, { points }),
  matchEditorScan: (id: string, key: string, to: string) =>
    request<ScanEditorState>(
      'POST',
      `/scan-editor/${encodeURIComponent(id)}/scans/${encodeURIComponent(key)}/match`,
      { to },
    ),
  /** D138: every file read again (a server's copied again), the pieces carried onto it. */
  refreshScanEditor: (id: string, pieces: ScanPiece[]) =>
    request<ScanEditorState>('POST', `/scan-editor/${encodeURIComponent(id)}/refresh`, { pieces }),
  /** D138: the joined scan written into the server terminal's directory, never over a file. */
  saveScanToServer: (id: string, server: string, name: string, text: string) =>
    request<{ path: string; renamed: boolean }>('POST', `/scan-editor/${encodeURIComponent(id)}/save-to-server`, {
      server,
      name,
      text,
    }),
  closeScanEditor: (id: string) => request<void>('DELETE', `/scan-editor/${encodeURIComponent(id)}`),
  /** A one-time token for a server's terminal WebSocket. */
  remoteTerminal: (server: string) => request<{ token: string }>('POST', '/remote/terminals', { server }),
  claudeStatus: () => request<ClaudeStatus>('GET', '/claude'),
  claudeLaunches: () => request<LaunchQuestion[]>('GET', '/claude/launches'),
  /** A one-time token for the WebSocket showing that launch's screen. */
  claudeLaunchView: (jobId: string) =>
    request<{ token: string }>('POST', `/claude/launches/${encodeURIComponent(jobId)}/view`),
  /** A one-time token for the panel's terminal WebSocket (D92). */
  claudeSession: (resume: boolean) => request<{ token: string }>('POST', '/claude/sessions', { resume }),
  /** A long poll: answers when something changed elsewhere, the requests changed, or after `wait` s. */
  live: (since: number | null, confirmVersion: number | null, wait: number, signal?: AbortSignal) => {
    const query = new URLSearchParams({ client: CLIENT_ID, wait: String(wait) })
    if (since !== null) query.set('since', String(since))
    if (confirmVersion !== null) query.set('confirmations', String(confirmVersion))
    return request<Live>('GET', `/live?${query.toString()}`, undefined, signal)
  },
  /** D107: a long poll that keeps this tab counted; answers at once when the server stops. */
  presence: (wait: number, signal?: AbortSignal) => {
    const query = new URLSearchParams({ tab: CLIENT_ID, wait: String(wait) })
    return request<Presence>('GET', `/presence?${query.toString()}`, undefined, signal)
  },
  health: () => request<{ status: string }>('GET', '/health'),
  shutdownInfo: () => request<ShutdownInfo>('GET', '/shutdown'),
  shutDown: () => request<{ sync: string | null }>('POST', '/shutdown'),
  reportSelection: (selection: SelectionReport) => request<void>('PUT', '/selection', selection),
  viewState: () => request<ViewState>('GET', '/view-state'),
  saveViewState: (state: ViewState) => request<void>('PUT', '/view-state', state),
  answerClaude: (id: string, confirm: boolean) =>
    request<ClaudeRequest>('POST', `/confirmations/${id}/answer`, { confirm }),

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
  /** D121: show a scan path node on an edge (null: on none); `on_edge_only` hides its box. */
  setPathEdge: (id: string, edge_id: string | null, on_edge_only?: boolean) =>
    request<Node>('PUT', `/nodes/${id}/path-edge`, { edge_id, ...(on_edge_only === undefined ? {} : { on_edge_only }) }),
  createNode: (
    fields: NodeFields & { kind?: NodeKind; xyz?: string; pos_x?: number; pos_y?: number; canvas_id?: string | null },
  ) =>
    request<Node>('POST', '/nodes', fields),
  updateNode: (id: string, fields: NodeFields) => request<Node>('PATCH', `/nodes/${id}`, fields),
  /** D139: `newNode` saves as a new node derived from this one even without calculations. */
  setGeometry: (id: string, xyz: string, newNode = false) =>
    request<{ node: Node; derived: boolean }>('PUT', `/nodes/${id}/geometry`, newNode ? { xyz, new_node: true } : { xyz }),
  builderParts: () => request<BuilderParts>('GET', '/builder/parts'),
  builderDescribe: (
    structure: BuilderStructure,
    picks: number[],
    charge: number | null,
    multiplicity: number | null,
    ignore: [number, number][] = [],
  ) => request<BuilderDescription>('POST', '/builder/describe', { ...structure, picks, charge, multiplicity, ignore }),
  /** D143: the structure in 2D for the Ketcher drawer. */
  builderDrawing: (structure: BuilderStructure) =>
    request<{ molfile: string }>('POST', '/builder/drawing', structure),
  builderEdit: (structure: BuilderStructure, picks: number[], tool: BuilderTool, options: BuilderOptions = {}) =>
    request<BuilderEdited>('POST', '/builder/edit', { ...structure, picks, tool, ...options }),
  deletePreview: (id: string) => request<NodeDeletePreview>('GET', `/nodes/${id}/delete-preview`),
  deleteNode: (id: string) => request<unknown>('DELETE', `/nodes/${id}`),
  xyzDownloadUrl: (id: string) => `/api/nodes/${id}/xyz`,

  history: (recordId?: string) =>
    request<HistoryEntry[]>(
      'GET',
      recordId ? `/history?record_id=${encodeURIComponent(recordId)}` : '/history',
    ),

  undoPreview: (entryId: number) => request<UndoPreview>('GET', `/history/${entryId}/undo`),
  undoImport: (entryId: number) => request<UndoPreview>('POST', `/history/${entryId}/undo`),

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
        | 'standard_state'
        | 'crest_count'
        | 'hydrogens'
        | 'steric_colours'
        | 'theme'
        | 'profile_style'
      >
    > & {
      /** D159: the open investigation's thermochemistry becomes the default for new ones. */
      thermo_as_default?: boolean
    },
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
  scanFolder: (folder: string, recursive: boolean) =>
    request<BatchPlan>('POST', '/batch-imports', { folder, recursive }),
  previewBatch: (token: string, options: BatchOptions) =>
    request<BatchPlan>('POST', `/batch-imports/${token}/preview`, options),
  commitBatch: (token: string, options: BatchOptions) =>
    request<BatchImported>('POST', `/batch-imports/${token}/commit`, options),
  cancelBatch: (token: string) => request<void>('DELETE', `/batch-imports/${token}`),

  calculations: (nodeId: string) => request<Calculation[]>('GET', `/nodes/${nodeId}/calculations`),
  updateCalculation: (
    id: string,
    fields: Partial<LevelFields> & { geometry_level_id?: string | null; notes?: string },
  ) => request<Calculation>('PATCH', `/calculations/${id}`, fields),
  levels: () => request<Level[]>('GET', '/levels'),
  customBases: () => request<CustomBasisSummary[]>('GET', '/custom-bases'),
  customBasis: (id: string) => request<CustomBasisDetail>('GET', `/custom-bases/${id}`),
  customBasisFileUrl: (id: string, elements?: string[]) =>
    `/api/custom-bases/${id}/file${elements ? `?elements=${encodeURIComponent(elements.join(','))}` : ''}`,
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

  // D152: the UI starts no outgoing branch; the API still takes one (the agent's create_group).
  createGroup: (member_ids: string[], label: string) =>
    request<Group>('POST', '/groups/reconnect', { member_ids, label, outgoing: null }),
  updateGroup: (id: string, fields: GroupFields) => request<Group>('PATCH', `/groups/${id}`, fields),
  addToGroup: (id: string, node_ids: string[]) => request<Group>('POST', `/groups/${id}/members`, { node_ids }),
  reorderGroup: (id: string, ids: string[]) => request<Group>('PUT', `/groups/${id}/order`, { ids }),
  removeFromGroup: (id: string, node_id: string) => request<Group>('DELETE', `/groups/${id}/members/${node_id}`),
  groupDeletePreview: (id: string) => request<GroupDeletePreview>('GET', `/groups/${id}/delete-preview`),
  dissolveGroup: (id: string, restore_branches: boolean) =>
    request<void>('POST', `/groups/${id}/dissolve`, { restore_branches }),
  deleteGroup: (id: string) => request<unknown>('DELETE', `/groups/${id}`),

  // D193: sub-investigations. `with_groups`: a member given alone moves its whole group (asked first).
  createSubInvestigation: (fields: {
    label: string
    ids: string[]
    face_id?: string | null
    with_groups?: boolean
    canvas_id?: string | null
    pos_x?: number
    pos_y?: number
  }) => request<SubInvestigation>('POST', '/sub-investigations', fields),
  moveToCanvas: (ids: string[], target_id: string | null, with_groups = false) =>
    request<{ moved: number }>('POST', '/sub-investigations/move', { ids, target_id, with_groups }),
  updateSubInvestigation: (id: string, fields: SubInvestigationFields) =>
    request<SubInvestigation>('PATCH', `/sub-investigations/${id}`, fields),
  dissolveSubInvestigation: (id: string) => request<{ moved: number }>('POST', `/sub-investigations/${id}/dissolve`),
  subDeletePreview: (id: string) => request<SubDeletePreview>('GET', `/sub-investigations/${id}/delete-preview`),
  deleteSubInvestigation: (id: string) => request<unknown>('DELETE', `/sub-investigations/${id}`),

  energyOptions: () => request<EnergyOptions>('GET', '/energies/options'),
  energyView: (level: string, type: EnergyType, reference: string | null, bsse = false) =>
    request<EnergyView>(
      'GET',
      `/energies/view?level=${encodeURIComponent(level)}&type=${type}` +
        (reference ? `&reference=${encodeURIComponent(reference)}` : '') +
        (bsse ? '&bsse=true' : ''),
    ),
  /** D189: `follow` false lists the ways on from the last id without walking. */
  extendPathway: (path: string[], branchId?: string | null, follow = true) =>
    request<Pathway>('POST', '/pathways/extend', { path, branch_id: branchId ?? null, follow }),
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
    paths: { ids: string[]; branch_id: string | null; shifts?: PathShift[]; colour?: string | null }[],
    referenceId: string | null,
    view: SharedViewState,
  ): Promise<{ blob: Blob; name: string }> => {
    const response = await fetch('/api/snapshot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths, reference_id: referenceId, view }),
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
  steps: (calculationId: string) => request<Steps>('GET', `/calculations/${calculationId}/steps`),
  irc: (nodeId: string) => request<Irc>('GET', `/nodes/${nodeId}/irc?structures=true`),
  makeIrcNode: (nodeId: string, direction: IrcDirection) =>
    request<{ node_id: string }>('POST', `/nodes/${nodeId}/irc/${direction}/node`),
  ircSummary: (nodeId: string) => request<IrcSummary>('GET', `/nodes/${nodeId}/irc/summary`),
  removeIrcFile: (nodeId: string, sourceFileId: string) =>
    request<void>('DELETE', `/nodes/${nodeId}/irc/files/${sourceFileId}`),
  useStep: (calculationId: string, frame: number) =>
    request<{ node: Node; derived: boolean }>('POST', `/calculations/${calculationId}/steps/${frame}/use`),
  trimSteps: (calculationId: string, body: { remove?: number[]; restore?: number[] }) =>
    request<{ steps: Steps; node: Node; moved: number | null }>('POST', `/calculations/${calculationId}/steps/trim`, body),
  previewTrim: (calculationId: string, body: { remove?: number[]; restore?: number[] }) =>
    request<Trim>('POST', `/calculations/${calculationId}/steps/trim-preview`, body),
  overlay: (body: OverlayRequest) => request<Overlay>('POST', '/overlay', body),
  scanPathPlan: (body: ScanPathRequest) => request<ScanPathPlan>('POST', '/scan-paths/plan', body),
  sendScanPath: (
    body: ScanPathRequest & {
      held: { end: 'start' | 'end'; atoms: number[] }[]
      /** D119: the user's own coordinates, run first as given. */
      drive?: { atoms: number[]; from: number; to: number }[]
      drive_order?: 'together' | 'staged'
      /** D196: coordinates kept fixed along the whole path. */
      constrain?: { atoms: number[]; value: number }[]
      solvent: string | null
    },
  ) =>
    request<{ job: CloudJob; start_error: string | null }>('POST', '/scan-paths', body),
  jobs: (refresh = false) => request<CloudJob[]>('GET', `/jobs?detail=true${refresh ? '&refresh=true' : ''}`),
  job: (id: string) => request<CloudJob>('GET', `/jobs/${encodeURIComponent(id)}?refresh=true`),
  startJob: (id: string) => request<CloudJob>('POST', `/jobs/${encodeURIComponent(id)}/start`),
  fetchJob: (id: string) =>
    request<{ branch: string; files: string[] }>('POST', `/jobs/${encodeURIComponent(id)}/fetch`),
  importPath: (id: string, again = false) =>
    request<{ node_id: string; label: string; on_edge: boolean }>(
      'POST',
      `/jobs/${encodeURIComponent(id)}/import-path${again ? '?again=true' : ''}`,
    ),
  atomMatch: (startId: string, endId: string, pairs: [number, number][] = []) =>
    request<AtomMatch>('POST', '/atom-match', { start_id: startId, end_id: endId, pairs }),
  energyProfiles: () => request<EnergyProfile[]>('GET', '/energy-profiles'),
  createEnergyProfile: (body: { name?: string; paths?: ProfilePath[] }) =>
    request<EnergyProfile>('POST', '/energy-profiles', body),
  updateEnergyProfile: (id: string, body: { name?: string; paths?: ProfilePath[] }) =>
    request<EnergyProfile>('PATCH', `/energy-profiles/${id}`, body),
  duplicateEnergyProfile: (id: string) => request<EnergyProfile>('POST', `/energy-profiles/${id}/duplicate`),
  deleteEnergyProfile: (id: string) => request<void>('DELETE', `/energy-profiles/${id}`),
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
  equilibria: () => request<Equilibrium[]>('GET', '/equilibria'),
  createEquilibrium: (fields: EquilibriumFields & { name: string }) =>
    request<Equilibrium>('POST', '/equilibria', fields),
  updateEquilibrium: (id: string, fields: EquilibriumFields) =>
    request<Equilibrium>('PATCH', `/equilibria/${id}`, fields),
  deleteEquilibrium: (id: string) => request<void>('DELETE', `/equilibria/${id}`),
  equilibriumResult: (id: string) => request<EquilibriumResult>('GET', `/equilibria/${id}/result`),
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
export type StandardState = '1 atm' | '1 M'

/** D95: G(1 M) − G(1 atm) of one molecule in hartree, RT ln(V_m / 1 L mol⁻¹) with V_m = RT / 1 atm;
 * the backend's `thermochem.standard_state_correction`, for the Settings text only. */
export function standardStateCorrection(temperature: number): number {
  const R = 8.31446261815323
  const joulesPerHartree = 4.184 * 627.5094740631 * 1000
  return (R * temperature * Math.log(((R * temperature) / 101325) * 1000)) / joulesPerHartree
}

/** The energy type as shown. G and G_qh always name their standard state, 1 atm included (D95,
 * D160). G is the files' own, at its frequency jobs' temperatures (`jobTemperatures`); G_qh is at
 * the G_qh temperature and cutoff (D58). */
export function energyTypeName(
  type: EnergyType,
  temperature?: number,
  cutoff?: number,
  standardState?: StandardState,
  jobTemperatures?: number[],
): string {
  const state = standardState ?? '1 atm'
  if (type === 'G') {
    const shown = (jobTemperatures ?? []).map((t) => `${t} K`).join(', ')
    return shown ? `G (${state}, ${shown})` : `G (${state})`
  }
  if (type !== 'G_qh') return type
  if (temperature === undefined) return `G_qh (${state})`
  return `G_qh (${temperature} K, ${cutoff} cm⁻¹, ${state})`
}

/** D160: G(c) − G(1 atm) of one molecule in hartree: the 1 M shift plus RT ln(c / 1 M); the
 * backend's `concentrations.correction`, for the node panel's text only. */
export function concentrationCorrection(temperature: number, value: Concentration): number {
  const molarity = value.kind === '1 M' ? 1 : (value.molarity ?? 1)
  const joulesPerHartree = 4.184 * 627.5094740631 * 1000
  return standardStateCorrection(temperature) + (8.31446261815323 * temperature * Math.log(molarity)) / joulesPerHartree
}

/** D160: a free species' concentration as shown, "neat THF, 12.3 M". */
export function concentrationText(value: Concentration): string {
  if (value.kind === '1 M') return '1 M'
  if (value.kind === 'molar') return `${value.molarity} M`
  return `neat ${value.solvent || 'solvent'}, ${value.molarity} M`
}

/** D139: the structure the Edit structure window holds: its atoms and the bonds the user added
 * or removed (atom pairs from 0), which decide which atoms move together. D140: the atoms changed
 * in the window and what Minimize keeps. */
export type BuilderStructure = {
  rows: GeometryRows
  added: [number, number][]
  removed: [number, number][]
  changed: number[]
  holds: BuilderHold[]
  /** D145: the coordinates Minimize takes the bonds and the metals' shape from, so Minimize
   * again stays put; Minimize sets it and any other change of the atoms drops it. */
  minimized_from?: GeometryRows | null
  /** D146: whether those Minimizes kept the metals' shape or relaxed them. */
  minimized_metals?: 'keep' | 'relax' | null
}

/** D140: one atom frozen, or a distance, angle or dihedral (2 to 4 atoms) held at `value`. */
export type BuilderHold = { atoms: number[]; value: number | null }

export type BuilderTool =
  | 'distance'
  | 'angle'
  | 'dihedral'
  | 'rotate'
  | 'bond'
  | 'element'
  | 'delete'
  | 'add_atom'
  | 'fragment'
  | 'molecule'
  | 'freeze'
  | 'unfreeze'
  | 'hold'
  | 'release'
  | 'minimize'
  | 'smiles'
  | 'hydrogens'
  | 'drawing'
  | 'redraw'
  | 'flip'

export type BuilderOptions = {
  value?: number
  /** The end atom whose side moves; left out: the smaller side. */
  move?: number | null
  element?: string
  name?: string
  atoms?: GeometryRows
  what?: string
  present?: boolean
  with_hydrogens?: boolean
  /** release: which hold. */
  index?: number
  /** minimize: every atom, not only the changed ones and two bonds round them. */
  everything?: boolean
  /** minimize: keep each metal's shape, or relax it to UFF's bond lengths and the nearest
   * ideal shape (D146). */
  metals?: 'keep' | 'relax'
  /** smiles, or fragment: a SMILES with one * where it attaches (D141). */
  smiles?: string
  /** drawing: Ketcher's MOL file, a fragment with an attachment point (D142); redraw: the
   * structure drawn again, put back onto it (D143). */
  molfile?: string
  /** hydrogens: until each picked atom has this many bonds; left out: its usual number. */
  bonds?: number
  /** flip: bonds (atom pairs from 0) left out when finding what turns (D166). */
  ignore?: [number, number][]
}

export type BuilderParts = { fragments: string[]; molecules: string[] }

export type BuilderDescription = {
  formula: string
  atom_count: number
  kind: 'distance' | 'angle' | 'dihedral' | null
  value: number | null
  bonded: boolean | null
  /** Each end's side: how many atoms move with it, and whether it is the whole side (false:
   * the sides are joined, so only the end atom and the atoms bonded to it alone move). */
  sides: { atom: number; count: number; whole: boolean }[]
  default_side: number | null
  electrons_note: string | null
  /** D166, three atoms picked: how many atoms Flip would turn; when the fragment is tied to the
   * rest, the bonds of the shortest way round (atom pairs from 0); why it cannot flip. */
  flip?: { count: number; tie: [number, number][]; error: string | null } | null
}

export type BuilderEdited = BuilderStructure & { picks: number[]; message: string }

/** Geometry as stored in history entries: [[element, x, y, z], ...]. */
export type GeometryRows = [string, number, number, number][]

export function geometryToXyz(rows: GeometryRows, comment = ''): string {
  const lines = rows.map(
    ([el, x, y, z]) =>
      `${el.padEnd(2)} ${x.toFixed(8).padStart(14)} ${y.toFixed(8).padStart(14)} ${z.toFixed(8).padStart(14)}`,
  )
  return [String(rows.length), comment, ...lines].join('\n') + '\n'
}
