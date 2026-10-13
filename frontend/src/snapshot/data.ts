// The data inside a read-only copy (D79), written by src/chembook3d/api/snapshot.py. Every
// record has the shape the API answers with, so the app's components can show it; everything
// that depends on a choice the reader can make (level, energy type, reference) is included
// for each choice (A30).

import type {
  Calculation,
  Canvas,
  EnergyOptions,
  EnergyTable,
  EnergyType,
  EnergyView,
  Modes,
  Overview,
  ProfileStyle,
  Profiles,
  Settings,
  SharedViewState,
  SourceFile,
} from '../api'
import { NO_FILTERS, type Filters, type ViewMode } from '../canvasView'
import type { HydrogenMode } from '../chem'
import type { DrawerPath } from '../components/EnergyDrawer'
import { SCREEN_STYLE } from '../profileStyle'

/** FR-SHARE-04: an imported file keeps its name, size and checksum; its paths stay behind. */
export type SharedSourceFile = Pick<SourceFile, 'original_name' | 'size' | 'checksum' | 'imported_at'>

export type SharedCalculation = Omit<Calculation, 'source_file'> & { source_file: SharedSourceFile | null }

export type SharedView = {
  values: EnergyView['values']
  edges: EnergyView['edges']
  /** ΔX of every node and group, for each reference the reader may choose. */
  relative: Record<string, EnergyView['relative']>
}

export type SnapshotData = {
  format: 'chembook3d-snapshot'
  version: number
  app_version: string
  /** D128: names this copy; copies made before D128 have none. */
  copy_id?: string
  exported_at: string
  investigation: { name: string }
  settings: Pick<
    Settings,
    | 'energy_unit'
    | 'energy_factors'
    | 'energy_decimals'
    | 'qh_temperature'
    | 'qh_cutoff'
    | 'standard_state'
    | 'hydrogens'
  > & {
    /** D106: copies made before profile styles have none and draw the "Screen" style. */
    profile_style?: ProfileStyle
  }
  canvas: Canvas
  /** D85: the pictures in pinned notes, as data URLs by id. */
  note_images: Record<string, string>
  overview: Overview
  calculations: Record<string, SharedCalculation[]>
  /** The imaginary and lowest real modes of each frequency calculation (A30). */
  modes: Record<string, Modes>
  energy_options: EnergyOptions
  /** By "level|type". */
  views: Record<string, SharedView>
  paths: DrawerPath[]
  reference_id: string | null
  /** D128: the view the app showed when the copy was made; copies made before have none. */
  view?: SharedViewState
  /** By "level|type|reference". */
  profiles: Record<string, { profiles: Profiles; table: EnergyTable }>
}

export const DATA_ELEMENT = 'chembook3d-snapshot-data'

/** The data is gzip-compressed JSON in base64; the browser unpacks it, offline. */
export async function readSnapshot(): Promise<SnapshotData> {
  const text = document.getElementById(DATA_ELEMENT)?.textContent?.trim() ?? ''
  if (!text || text.startsWith('__')) throw new Error('This page holds no investigation. Export one from Chembook3D.')
  const bytes = Uint8Array.from(atob(text), (c) => c.charCodeAt(0))
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))
  const data = JSON.parse(await new Response(stream).text()) as SnapshotData
  if (data.format !== 'chembook3d-snapshot') throw new Error('This page holds no Chembook3D investigation.')
  return data
}

/** The settings the shared components read; the rest never matters in a copy. */
export function sharedSettings(data: SnapshotData): Settings {
  return {
    ...data.settings,
    energy_units: [data.settings.energy_unit],
    recent: [],
    last_device: '',
    last_import_folder: '',
    geometry_tolerance: 0,
    duplicate_tolerance: 0,
    crest_count: 0,
    hydrogen_modes: ['all', 'polar', 'none'],
    steric_colours: 'blue',
    // Copies made before D95 have no standard state: they are at 1 atm.
    standard_state: data.settings.standard_state ?? '1 atm',
    standard_states: ['1 atm', '1 M'],
    profile_style: data.settings.profile_style ?? SCREEN_STYLE,
    profile_presets: { screen: SCREEN_STYLE, publication: SCREEN_STYLE },
  }
}

/** What the copy shows, which a reader may change (D128). */
export type ReaderView = {
  mode: ViewMode
  level: string | null
  energyType: EnergyType
  edgeEnergies: boolean
  referenceId: string | null
  filters: Filters
  expanded: string[]
  drawerOpen: boolean
  drawerTab: 'profile' | 'table'
  hydrogens: HydrogenMode
}

const READER_KEY = 'chembook3d.copy.'

function strings(value: unknown): string[] | undefined {
  return Array.isArray(value) && value.every((v) => typeof v === 'string') ? value : undefined
}

/** A reader's own choices in this copy, kept in their browser (D128); nothing if unreadable. */
function storedView(data: SnapshotData): Partial<ReaderView> {
  if (!data.copy_id) return {}
  let raw: unknown
  try {
    raw = JSON.parse(localStorage.getItem(READER_KEY + data.copy_id) ?? 'null')
  } catch {
    return {}
  }
  if (!raw || typeof raw !== 'object') return {}
  const stored = raw as Record<string, unknown>
  const found: Partial<ReaderView> = {}
  if (stored.mode === 'compact' || stored.mode === 'energy' || stored.mode === 'structure') found.mode = stored.mode
  if (typeof stored.level === 'string' || stored.level === null) found.level = stored.level
  if (typeof stored.energyType === 'string') found.energyType = stored.energyType as EnergyType
  if (typeof stored.edgeEnergies === 'boolean') found.edgeEnergies = stored.edgeEnergies
  if (stored.referenceId === null || (typeof stored.referenceId === 'string' && referenceIds(data).has(stored.referenceId)))
    found.referenceId = stored.referenceId
  const filters = stored.filters as Record<string, unknown> | null | undefined
  if (filters && typeof filters === 'object') {
    const [branches, statuses, steps] = [strings(filters.branches), strings(filters.statuses), strings(filters.steps)]
    if (branches && statuses && steps) found.filters = { branches, statuses, steps }
  }
  const expanded = strings(stored.expanded)
  if (expanded) found.expanded = expanded
  if (typeof stored.drawerOpen === 'boolean') found.drawerOpen = stored.drawerOpen
  if (stored.drawerTab === 'profile' || stored.drawerTab === 'table') found.drawerTab = stored.drawerTab
  if (stored.hydrogens === 'all' || stored.hydrogens === 'polar' || stored.hydrogens === 'none')
    found.hydrogens = stored.hydrogens
  return found
}

/** D128: the copy opens on the view the app showed when it was made, then on what this
 * reader last chose in it. Copies made before D128 open as they always did. */
export function openingView(data: SnapshotData): ReaderView {
  const view = data.view
  const shown: ReaderView = {
    mode: view?.mode ?? (data.reference_id ? 'energy' : 'compact'),
    level: view?.level ?? null,
    energyType: view?.energy_type ?? 'G',
    edgeEnergies: view?.edge_energies ?? true,
    referenceId: data.reference_id,
    filters: view?.filters ?? NO_FILTERS,
    expanded: view?.expanded ?? [],
    drawerOpen: view?.drawer.open ?? false,
    drawerTab: view?.drawer.tab ?? 'profile',
    hydrogens: data.settings.hydrogens,
  }
  const opened = { ...shown, ...storedView(data) }
  const groups = new Set(data.canvas.groups.map((g) => g.id))
  return { ...opened, expanded: opened.expanded.filter((id) => groups.has(id)) }
}

/** Remembers the reader's choices for this copy in their browser (a convenience only). */
export function rememberView(data: SnapshotData, view: ReaderView) {
  if (!data.copy_id) return
  try {
    localStorage.setItem(READER_KEY + data.copy_id, JSON.stringify(view))
  } catch {
    // private windows and blocked storage: the copy still works, it just forgets
  }
}

/** The energy view the canvas shows, put together from the parts in the copy. */
export function energyView(
  data: SnapshotData,
  level: string,
  type: EnergyType,
  referenceId: string | null,
): EnergyView | null {
  const found = data.views[`${level}|${type}`]
  if (!found) return null
  const relative = referenceId ? found.relative[referenceId] : undefined
  return {
    level,
    type,
    values: found.values,
    edges: found.edges,
    reference_id: relative ? referenceId : null,
    relative: relative ?? {},
  }
}

/** The references the reader can choose (A30): the same for every level and type. */
export function referenceIds(data: SnapshotData): Set<string> {
  const first = Object.values(data.views)[0]
  return new Set(first ? Object.keys(first.relative) : [])
}

/** The energy table as CSV text, quoted as the app's own export (T-EN-08). */
export function tableCsv(table: EnergyTable): string {
  const cell = (text: string) => (/[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text)
  return '﻿' + [table.columns, ...table.rows].map((row) => row.map(cell).join(',')).join('\r\n') + '\r\n'
}
