import { useEffect, useState } from 'react'
import type { HistoryEntry, UndoFile, UndoPreview } from '../api'
import { api, ApiError, CALCULATION_TYPES } from '../api'
import { Modal } from './Modal'

const FIELD_NAMES: Record<string, string> = {
  geometry: 'coordinates',
  charge: 'charge',
  multiplicity: 'multiplicity',
  label: 'label',
  role: 'role',
  status: 'status',
}

const quoted = (label: string) => (label ? `“${label}”` : 'an untitled node')

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

function list(items: string[]): string {
  return items.length <= 1 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** What undoing one file does, one line per kind of change. */
function FileChanges({ file }: { file: UndoFile }) {
  const types = [...new Set(file.calculations.map((c) => (CALCULATION_TYPES[c.type] ?? c.type).toLowerCase()))]
  const restored = new Map<string, { label: string; fields: string[] }>()
  for (const r of file.restored) {
    const entry = restored.get(r.node_id) ?? { label: r.label, fields: [] }
    entry.fields.push(FIELD_NAMES[r.field] ?? r.field)
    restored.set(r.node_id, entry)
  }
  return (
    <ul>
      <li>
        Removes {plural(file.calculations.length, 'calculation')}
        {types.length > 0 && ` (${list(types)})`} and the stored copy of {file.file}
      </li>
      {file.group && (
        <li>
          Deletes the group {quoted(file.group.label)} with its {plural(file.deleted.length, 'member')}
        </li>
      )}
      {!file.group && file.deleted.length > 0 && (
        <li>
          Deletes the {file.deleted.length === 1 ? 'node' : 'nodes'} it made: {list(file.deleted.map((n) => quoted(n.label)))}
        </li>
      )}
      {[...restored.values()].map((r) => (
        <li key={r.label + r.fields.join()}>
          Puts back the {list(r.fields)} {quoted(r.label)} had before the import
        </li>
      ))}
      {file.tags.map((t) => (
        <li key={t.node_id}>
          {t.removed.length > 0 && `Takes the tag ${list(t.removed)} off ${quoted(t.label)}`}
          {t.removed.length > 0 && t.added.length > 0 && '; '}
          {t.added.length > 0 && `Puts the tag ${list(t.added)} back on ${quoted(t.label)}`}
        </li>
      ))}
      {file.kept.map((k) => (
        <li key={k.node_id + k.field}>
          Keeps the {FIELD_NAMES[k.field] ?? k.field} of {quoted(k.label)} as it is: it was changed after the import
        </li>
      ))}
    </ul>
  )
}

/** D102: confirm undoing an import from its history entry, after showing what it removes and
 * puts back, or what blocks it. */
export function UndoImportDialog({
  entry,
  onClose,
  onDone,
}: {
  entry: HistoryEntry
  onClose: () => void
  onDone: (result: UndoPreview) => void
}) {
  const [preview, setPreview] = useState<UndoPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.undoPreview(entry.id).then(setPreview, (err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [entry.id])

  const batch = entry.undo === 'batch'
  const blockers = preview?.blockers ?? []
  const confirm = () => {
    setBusy(true)
    api.undoImport(entry.id).then(
      (result) => onDone(result),
      (err: unknown) => {
        setBusy(false)
        if (err instanceof ApiError && err.blockers && preview) setPreview({ ...preview, blockers: err.blockers })
        else setError(err instanceof Error ? err.message : String(err))
      },
    )
  }

  return (
    <Modal
      title={batch ? 'Undo batch import?' : 'Undo import?'}
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>{blockers.length ? 'Close' : 'Cancel'}</button>
          {preview && !blockers.length && (
            <button className="danger" disabled={busy} onClick={confirm}>
              {batch ? 'Undo batch' : 'Undo import'}
            </button>
          )}
        </>
      }
    >
      {error && <p className="error">{error}</p>}
      {!preview && !error && <p className="muted">Checking what depends on this import…</p>}
      {preview && blockers.length > 0 && (
        <>
          <p>This import cannot be undone while later work builds on it:</p>
          <ul aria-label="What blocks the undo">
            {blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          <p className="muted">Undo or remove those first, then try again.</p>
        </>
      )}
      {preview && !blockers.length && preview.kind === 'import' && <FileChanges file={preview.files[0]} />}
      {preview && !blockers.length && preview.kind === 'batch' && (
        <>
          <p>
            Undoes the {plural(preview.files.length, 'file')} still left from {preview.folder ?? 'the folder'}, last
            one first:
          </p>
          {preview.files.map((file) => (
            <section key={file.source_file_id} aria-label={`Undo ${file.file}`}>
              <h3>{file.file}</h3>
              <FileChanges file={file} />
            </section>
          ))}
        </>
      )}
      {preview && !blockers.length && (
        <p className="muted">The history keeps the import and records the undo.</p>
      )}
    </Modal>
  )
}
