import type { GeometryRows, HistoryEntry } from '../api'
import { CALCULATION_TYPES, NOTE_CORNER_LABEL, ROLES, speciesChip, STATUSES, type NoteCorner } from '../api'
import { noteTitle } from '../notes'

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
  if (field === 'outcomes') {
    // D83: a selectivity's outcomes, by name
    const outcomes = value as { name: string }[]
    return outcomes.length ? outcomes.map((o) => o.name).join(', ') : 'none'
  }
  if (field === 'member_ids') {
    // D89: a group's members, in order
    const ids = value as string[]
    return ids.length ? ids.map((id) => named(names, id)).join(', ') : 'none'
  }
  if (field === 'path') {
    // D86: a turnover's pathway, in order
    const ids = value as string[]
    return ids.length ? ids.map((id) => named(names, id)).join(' → ') : 'none'
  }
  if (field === 'compare_id') return 'another turnover'
  if (field === 'conformers') return value === 'lowest' ? 'lowest TS only' : 'Boltzmann sum'
  if (field === 'temperature') return `${String(value)} K`
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
  member_ids: 'Member order',
  outgoing_branch_id: 'Outgoing branch',
  incoming_branch_ids: 'Incoming branches',
  species: 'Free species',
  level: 'Level of theory',
  energy_type: 'Energy',
  temperature: 'Temperature',
  conformers: 'Conformers',
  excess: 'Shown for two outcomes',
  outcomes: 'Outcomes',
  path: 'Pathway',
  compare_id: 'Compared with',
}

const STRUCTURE_TYPES: Record<string, string> = {
  step: 'step',
  branch: 'branch',
  transition: 'transition',
  group: 'group',
  selectivity: 'selectivity',
  turnover: 'turnover',
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
  if (entry.action === 'remove_member' && value) {
    const after = new Set((value.member_ids as string[]) ?? [])
    const before = ((entry.old_value as Record<string, unknown> | null)?.member_ids as string[]) ?? []
    const removed = before.filter((id) => !after.has(id))
    return `Took ${named(names, removed)} out of group “${title || 'Group'}”`
  }
  if (entry.action === 'dissolve') return `Dissolved group “${title || 'Group'}”`
  if (entry.action === 'create') return `Created ${kind}${title ? ` “${title}”` : ''}`
  if (entry.action === 'delete') return `Deleted ${kind}${title ? ` “${title}”` : ''}`
  const field = entry.field ?? ''
  const label = STRUCTURE_FIELDS[field] ?? field
  if (field === 'notes' || field === 'level') return `${label} of the ${kind} changed`
  if (field === 'outcomes' && show(field, entry.old_value) === show(field, entry.new_value)) {
    return `Outcomes ${show(field, entry.new_value)}: transition states or experiment changed`
  }
  if (field === 'temperature' && entry.new_value === null) return 'Temperature: back to the G_qh setting'
  if (field === 'temperature' && entry.old_value === null) return `Temperature: G_qh setting → ${show(field, entry.new_value)}`
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
  if (entry.record_type === 'batch_import' && value) {
    // D97: one entry for a whole folder; each file's own records follow it.
    const files = (value.files as { file: string }[] | undefined) ?? []
    return `Imported ${files.length} file${files.length === 1 ? '' : 's'} from ${String(value.folder)}: ${files.map((f) => f.file).join(', ')}`
  }
  if (entry.record_type === 'custom_basis' || entry.record_type === 'custom_dispersion') {
    const what = entry.record_type === 'custom_basis' ? 'custom basis set' : 'custom dispersion'
    if (entry.action === 'create' && value) return `Named a ${what} “${String(value.name)}”`
    return `Added elements to a ${what}: ${show(null, entry.new_value)}`
  }
  return null
}

/** D85, A35: a pinned note's changes, recorded on its node. */
function describeNote(entry: HistoryEntry): string | null {
  if (entry.record_type !== 'note') return null
  const value = (entry.new_value ?? entry.old_value) as { title?: string; body?: string } | null
  const name = `“${noteTitle({ title: value?.title ?? '', body: value?.body ?? '' })}”`
  if (entry.action === 'create') return `Pinned a note ${name}`
  if (entry.action === 'delete') return `Deleted the pinned note ${name}`
  const field = entry.field ?? ''
  const before = (entry.old_value as Record<string, unknown> | null)?.[field]
  const after = (entry.new_value as Record<string, unknown> | null)?.[field]
  const title = (entry.new_value as { title?: string } | null)?.title
  const named = title ? ` “${title}”` : ''
  if (field === 'body') return `Text of the pinned note${named} changed`
  if (field === 'title') return `Pinned note title: “${String(before ?? '')}” → “${String(after ?? '')}”`
  if (field === 'corner') {
    const corner = (c: unknown) => NOTE_CORNER_LABEL[c as NoteCorner]?.toLowerCase() ?? String(c)
    return `Pinned note${named} moved: ${corner(before)} → ${corner(after)}`
  }
  return `Pinned note${named} ${field}: ${String(before)} → ${String(after)}`
}

function describe(entry: HistoryEntry, names: Names): string {
  const other = describeNote(entry) ?? describeOther(entry) ?? describeStructure(entry, names)
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
    if (entry.old_value == null) return `Coordinates added (${show(field, entry.new_value)})`
    if (entry.new_value == null) return `Coordinates removed (${show(field, entry.old_value)})`
    return `Coordinates changed (${show(field, entry.old_value)} → ${show(field, entry.new_value)})`
  }
  return `${FIELD_LABELS[field] ?? field}: ${show(field, entry.old_value, names)} → ${show(field, entry.new_value, names)}`
}

function canRestore(entry: HistoryEntry): boolean {
  if (entry.record_type !== 'node') return false
  if (entry.action !== 'update' || !entry.field || !(entry.field in FIELD_LABELS)) return false
  // D90: restoring to no coordinates removes them (refused on a node with calculations).
  return entry.field !== 'geometry' || Array.isArray(entry.old_value) || entry.old_value == null
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
  // A pinned note's changes are recorded on its node (D85).
  const nodeId =
    entry.record_type === 'node' || entry.record_type === 'note' ? entry.record_id : (value?.node_id ?? null)
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
