import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type FolderListing } from '../api'
import { Modal } from './Modal'

function join(parent: string, name: string): string {
  const sep = parent.includes('\\') && !parent.includes('/') ? '\\' : '/'
  return parent.endsWith(sep) ? parent + name : parent + sep + name
}

/** Choose a folder on this computer (FR-INV-02). A web page cannot read local paths, so the
 * backend lists folders. In "create" mode the user picks a parent folder and a new name. */
export function FolderPicker({
  mode,
  onCancel,
  onChoose,
  title,
  chooseLabel,
  initialName = '',
}: {
  mode: 'open' | 'create'
  onCancel: () => void
  onChoose: (folder: string, name: string) => void
  /** For other uses of a new folder, such as Open from GitHub (D71). */
  title?: string
  chooseLabel?: string
  initialName?: string
}) {
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [pathInput, setPathInput] = useState('')
  const [name, setName] = useState(initialName)
  const [error, setError] = useState<string | null>(null)

  // Only the latest request may update the dialog: listing the home folder can finish after
  // a folder the user asked for later. A path typed while a listing loads is kept.
  const latest = useRef(0)
  const typed = useRef(false)
  const browse = useCallback((path?: string) => {
    const request = ++latest.current
    typed.current = false
    api
      .folders(path)
      .then((result) => {
        if (request !== latest.current) return
        setListing(result)
        if (!typed.current) setPathInput(result.path)
        setError(null)
      })
      .catch((err: unknown) => {
        if (request !== latest.current) return
        setError(err instanceof ApiError ? err.message : String(err))
      })
  }, [])

  useEffect(() => browse(), [browse])

  const trimmed = name.trim()
  const canChoose =
    listing !== null && (mode === 'open' ? listing.is_investigation : trimmed.length > 0)
  const choose = () => {
    if (!listing || !canChoose) return
    if (mode === 'open') onChoose(listing.path, '')
    else onChoose(join(listing.path, trimmed), trimmed)
  }

  return (
    <Modal
      title={title ?? (mode === 'open' ? 'Open investigation' : 'New investigation')}
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={!canChoose} onClick={choose}>
            {chooseLabel ?? (mode === 'open' ? 'Open' : 'Create')}
          </button>
        </>
      }
    >
      <form
        className="path-row"
        onSubmit={(event) => {
          event.preventDefault()
          browse(pathInput)
        }}
      >
        <input
          aria-label="Folder path"
          value={pathInput}
          onChange={(event) => {
            typed.current = true
            setPathInput(event.target.value)
          }}
        />
        <button type="submit">Go</button>
      </form>
      {listing && (
        <div className="roots">
          {listing.roots.map((root) => (
            <button key={root} className="link" onClick={() => browse(root)}>
              {root}
            </button>
          ))}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {listing && (
        <ul className="folder-list" aria-label="Folders">
          {listing.parent && (
            <li>
              <button className="folder" onClick={() => browse(listing.parent!)}>
                ↑ Up
              </button>
            </li>
          )}
          {listing.entries.map((entry) => (
            <li key={entry.path}>
              <button className="folder" onClick={() => browse(entry.path)}>
                📁 {entry.name}
                {entry.is_investigation && <span className="badge">investigation</span>}
              </button>
            </li>
          ))}
          {listing.entries.length === 0 && <li className="muted">No subfolders</li>}
        </ul>
      )}
      {mode === 'open' && listing && !listing.is_investigation && (
        <p className="muted">Browse into a folder marked “investigation” to open it.</p>
      )}
      {mode === 'create' && (
        <label className="field">
          <span>Name of the new investigation folder</span>
          <input
            aria-label="Investigation name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') choose()
            }}
            placeholder="e.g. Ru-CAAC metathesis"
          />
        </label>
      )}
      {mode === 'create' && listing && trimmed && (
        <p className="muted">
          Creates <code>{join(listing.path, trimmed)}</code>
        </p>
      )}
    </Modal>
  )
}
