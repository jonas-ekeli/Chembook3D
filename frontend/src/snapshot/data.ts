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
  SourceFile,
} from '../api'
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
