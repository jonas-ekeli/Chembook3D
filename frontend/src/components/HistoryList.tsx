import type { GeometryRows, HistoryEntry } from '../api'
import { CALCULATION_TYPES, ROLES, speciesChip, STATUSES } from '../api'

const FIELD_LABELS: Record<string, string> = {
  label: 'Label',
  role: 'Role',
  charge: 'Charge',
  multiplicity: 'Multiplicity',
  status: 'Status',
  tags: 'Tags',
  notes: 'Notes',
  geometry: 'Coordinates',
  step_id: 'Step',
  branch_id: 'Branch',
  group_id: 'Group',
}

/** Names of steps, branches, groups and nodes, to show ids in history entries readably. */
export type Names = Map<string, string>

function named(names: Names, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.length ? value.map((v) => named(names, v)).join(', ') : '—'
  return names.get(String(value)) || 'a deleted record'
}

function show(field: string | null, value: unknown, names: Names = new Map()): string {
  if (value === null || value === undefined || value === '') return '—'
  if (field === 'status') return STATUSES.find((s) => s.value === value)?.label ?? String(value)
  if (field === 'role') return ROLES.find((r) => r.value === value)?.label ?? String(value)
  if (field === 'tags') return (value as string[]).length ? (value as string[]).join(', ') : '—'
  if (field === 'geometry') return `${(value as GeometryRows).length} atoms`
  if (field === 'kind') return value === 'species' ? 'free species' : 'node'
  if (field === 'species') {
    // D69: the free species on a transition
    const entries = value as { species_id: string; direction: 'joins' | 'leaves'; count: number }[]
    if (!entries.length) return 'none'
    return entries
      .map((e) => speciesChip({ ...e, label: names.get(e.species_id) || 'a deleted species' }))
      .join(', ')
  }
  if (field && ID_FIELDS.has(field)) return named(names, value)
  if (field === 'notes') {
    const text = String(value)
    return text.length > 60 ? `“${text.slice(0, 60)}…”` : `“${text}”`
  }
  return String(value)
}

const ID_FIELDS = new Set([
  'step_id',
  'branch_id',
  'group_id',
  'representative_id',
  'outgoing_branch_id',
  'parent_ids',
  'incoming_branch_ids',
])

const STRUCTURE_FIELDS: Record<string, string> = {
  name: 'Name',
  label: 'Label',
  notes: 'Notes',
  status: 'Status',
  colour: 'Colour',
  position: 'Position',
  parent_ids: 'Parents',
  step_id: 'Step',
  representative_id: 'Representative',
  outgoing_branch_id: 'Outgoing branch',
  incoming_branch_ids: 'Incoming branches',
  species: 'Free species',
}

const STRUCTURE_TYPES: Record<string, string> = {
  step: 'step',
  branch: 'branch',
  transition: 'transition',
  group: 'group',
}

function describeStructure(entry: HistoryEntry, names: Names): string | null {
  const kind = STRUCTURE_TYPES[entry.record_type]
  if (!kind) return null
  const value = (entry.new_value ?? entry.old_value) as Record<string, unknown> | null
  const title = String(value?.name ?? value?.label ?? '')
  if (entry.record_type === 'transition' && value && entry.action !== 'update') {
    const ends = `${named(names, value.source_id)} → ${named(names, value.target_id)}`
    return entry.action === 'create' ? `Transition added: ${ends}` : `Transition deleted: ${ends}`
  }
  if (entry.action === 'reconnect' && value) {
    const members = (value.member_ids as string[]) ?? []
    return `Reconnected ${members.length} nodes as group “${title || 'Group'}” (${named(names, members)})`
  }
  if (entry.action === 'add_members' && value) {
    const before = new Set(((entry.old_value as Record<string, unknown> | null)?.member_ids as string[]) ?? [])
    const added = ((value.member_ids as string[]) ?? []).filter((id) => !before.has(id))
    return `Added ${added.length} node${added.length === 1 ? '' : 's'} to group “${title || 'Group'}” (${named(names, added)})`
  }
  if (entry.action === 'dissolve') return `Dissolved group “${title || 'Group'}”`
  if (entry.action === 'create') return `Created ${kind}${title ? ` “${title}”` : ''}`
  if (entry.action === 'delete') return `Deleted ${kind}${title ? ` “${title}”` : ''}`
  const field = entry.field ?? ''
  const label = STRUCTURE_FIELDS[field] ?? field
  if (field === 'notes') return `${label} of the ${kind} changed`
  return `${label}: ${show(field, entry.old_value, names)} → ${show(field, entry.new_value, names)}`
}

const CALCULATION_FIELDS: Record<string, string> = {
  level: 'Level of theory',
  geometry_level: 'Geometry level',
  notes: 'Calculation notes',
}

function describeOther(entry: HistoryEntry): string | null {
  const value = entry.new_value as Record<string, unknown> | null
  if (entry.record_type === 'calculation') {
    if (entry.action === 'create' && value) {
      const type = CALCULATION_TYPES[String(value.type)] ?? String(value.type)
      return `Imported ${type.startsWith('TS') ? type : type.toLowerCase()} at ${String(value.level)} from ${String(value.file)} (step ${String(value.step)})`
    }
    const field = CALCULATION_FIELDS[entry.field ?? ''] ?? entry.field
    if (entry.field === 'notes') return `${field} changed`
    return `${field}: ${show(null, entry.old_value)} → ${show(null, entry.new_value)}`
  }
  if (entry.record_type === 'source_file') {
    const names: Record<string, string> = {
      original_name: 'File name',
      origin_device: 'Origin device',
      origin_path: 'Origin path',
    }
    return `${names[entry.field ?? ''] ?? entry.field}: ${show(null, entry.old_value)} → ${show(null, entry.new_value)}`
  }
  if (entry.record_type === 'custom_basis' || entry.record_type === 'custom_dispersion') {
    const what = entry.record_type === 'custom_basis' ? 'custom basis set' : 'custom dispersion'
    if (entry.action === 'create' && value) return `Named a ${what} “${String(value.name)}”`
    return `Added elements to a ${what}: ${show(null, entry.new_value)}`
  }
  return null
}

function describe(entry: HistoryEntry, names: Names): string {
  const other = describeOther(entry) ?? describeStructure(entry, names)
  if (other !== null) return other
  if (entry.action === 'split') return `Split into branches ${named(names, entry.new_value)}`
  if (entry.action === 'create') {
    const snapshot = entry.new_value as { label?: string; derived_from_id?: string | null } | null
    const what = snapshot?.derived_from_id ? 'Created as a derived node' : 'Created'
    return snapshot?.label ? `${what}: “${snapshot.label}”` : what
  }
  if (entry.action === 'delete') {
    const snapshot = entry.old_value as { label?: string } | null
    return snapshot?.label ? `Deleted “${snapshot.label}”` : 'Deleted'
  }
  const field = entry.field ?? ''
  if (field === 'geometry') {
    return `Coordinates changed (${show(field, entry.old_value)} → ${show(field, entry.new_value)})`
  }
  return `${FIELD_LABELS[field] ?? field}: ${show(field, entry.old_value, names)} → ${show(field, entry.new_value, names)}`
}

function canRestore(entry: HistoryEntry): boolean {
  if (entry.record_type !== 'node') return false
  if (entry.action !== 'update' || !entry.field || !(entry.field in FIELD_LABELS)) return false
  return entry.field !== 'geometry' || Array.isArray(entry.old_value)
}

/** The node an entry belongs to: the record itself, or the node a calculation was added to. */
function RecordLink({
  entry,
  labels,
  names,
  onSelect,
}: {
  entry: HistoryEntry
  labels: Map<string, string>
  names: Names
  onSelect?: (recordId: string) => void
}) {
  const value = entry.new_value as { node_id?: string } | null
  const nodeId = entry.record_type === 'node' ? entry.record_id : (value?.node_id ?? null)
  if (STRUCTURE_TYPES[entry.record_type]) {
    const name = names.get(entry.record_id)
    return <span className="muted">{name ? `${STRUCTURE_TYPES[entry.record_type]} ${name}` : STRUCTURE_TYPES[entry.record_type]}</span>
  }
  if (nodeId && labels.has(nodeId) && onSelect) {
    return (
      <button className="link" onClick={() => onSelect(nodeId)}>
        {labels.get(nodeId) || 'Untitled node'}
      </button>
    )
  }
  if (entry.record_type !== 'node' && !nodeId) return <span className="muted">—</span>
  return <span className="muted">{(nodeId && labels.get(nodeId)) ?? 'deleted node'}</span>
}

/** Append-only change history (FR-HIST-01…03). Restoring an old value makes a new change;
 * entries themselves are never edited (P15). */
export function HistoryList({
  entries,
  labels,
  names: known,
  onRestore,
  onSelect,
}: {
  entries: HistoryEntry[]
  labels?: Map<string, string>
  names?: Names
  onRestore?: (entry: HistoryEntry) => void
  onSelect?: (recordId: string) => void
}) {
  const names = known ?? labels ?? new Map<string, string>()
  if (entries.length === 0) return <p className="muted">No changes recorded yet.</p>
  return (
    <ol className="history" aria-label="History">
      {entries.map((entry) => (
        <li key={entry.id}>
          <time dateTime={entry.timestamp} title={entry.timestamp}>
            {new Date(entry.timestamp).toLocaleString()}
          </time>
          {labels && (
            <span className="record">
              <RecordLink entry={entry} labels={labels} names={names} onSelect={onSelect} />
            </span>
          )}
          <span className="what">{describe(entry, names)}</span>
          {entry.source !== 'manual' && <span className="badge">{entry.source}</span>}
          {onRestore && canRestore(entry) && (
            <button className="small" onClick={() => onRestore(entry)}>
              Restore old value
            </button>
          )}
        </li>
      ))}
    </ol>
  )
}
