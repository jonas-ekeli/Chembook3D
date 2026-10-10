import { useEffect, useState } from 'react'
import { api, ApiError, type RemoteEntry, type RemoteListing, type RemoteServer } from '../api'
import { Modal } from './Modal'

// D122e: "Import from here" lists the files in the terminal's current directory on a server.
// One chosen output opens the import dialog; several open the batch import (D97). The files
// are copied down only then, into the investigation as any import is.

export type RemoteImport = { server: string; paths: string[]; batch: boolean }

function size(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} kB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function when(seconds: number | null): string {
  return seconds === null
    ? ''
    : new Date(seconds * 1000).toLocaleString([], {
        dateStyle: 'short',
        timeStyle: 'short',
      })
}

function join(directory: string, name: string): string {
  return directory.endsWith('/') ? directory + name : `${directory}/${name}`
}

function parent(directory: string): string {
  const cut = directory.replace(/\/+$/, '').lastIndexOf('/')
  return cut <= 0 ? '/' : directory.slice(0, cut)
}

export function RemoteFilesDialog({
  server,
  onClose,
  onImport,
}: {
  server: RemoteServer
  onClose: () => void
  onImport: (request: RemoteImport) => void
}) {
  const [listing, setListing] = useState<RemoteListing | null>(null)
  const [path, setPath] = useState<string | undefined>(undefined) // undefined: the terminal's directory
  const [all, setAll] = useState(false)
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let stale = false
    api.remoteFiles(server.id, path).then(
      (result) => {
        if (stale) return
        setListing(result)
        setChosen(new Set())
        setError(null)
      },
      (err: unknown) => {
        if (!stale) setError(err instanceof ApiError && typeof err.detail === 'string' ? err.detail : String(err))
      },
    )
    return () => {
      stale = true
    }
  }, [server.id, path])

  const shown: RemoteEntry[] = listing
    ? listing.entries.filter((e) => e.kind === 'directory' || all || e.output || chosen.has(e.name))
    : []
  const files = shown.filter((e) => e.kind === 'file')
  const hidden = listing ? listing.entries.filter((e) => e.kind === 'file').length - files.length : 0
  const toggle = (name: string) => {
    const next = new Set(chosen)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setChosen(next)
  }
  const paths = listing ? [...chosen].sort().map((name) => join(listing.path, name)) : []

  return (
    <Modal
      title={`Import from ${server.name}`}
      onClose={onClose}
      wide
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            disabled={paths.length === 0}
            onClick={() => onImport({ server: server.id, paths, batch: true })}
            title="Match each file to a node by its structure or name, as a folder import does"
          >
            Batch import{paths.length > 0 ? ` (${paths.length})` : ''}
          </button>
          <button
            className="primary"
            disabled={paths.length !== 1}
            onClick={() => onImport({ server: server.id, paths, batch: false })}
          >
            Import
          </button>
        </>
      }
    >
      <div className="remote-files" aria-label="Files on the server">
        <p>
          <code>{listing?.path ?? server.cwd ?? '…'}</code>
          {listing && listing.path !== '/' && (
            <button className="link" onClick={() => setPath(parent(listing.path))}>
              Up
            </button>
          )}
          {path !== undefined && (
            <button className="link" onClick={() => setPath(undefined)}>
              Back to the terminal's directory
            </button>
          )}
        </p>
        {error && <p role="alert">{error}</p>}
        {!listing && !error && <p className="muted">Reading the directory…</p>}
        {listing && (
          <>
            <label className="check">
              <input type="checkbox" checked={all} onChange={(event) => setAll(event.target.checked)} />
              Show every file{hidden > 0 && !all ? ` (${hidden} more)` : ''}
            </label>
            <div className="remote-file-scroll">
              <table className="remote-file-table" aria-label="Files">
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        aria-label="Choose every file shown"
                        checked={files.length > 0 && files.every((f) => chosen.has(f.name))}
                        onChange={(event) =>
                          setChosen(event.target.checked ? new Set(files.map((f) => f.name)) : new Set())
                        }
                      />
                    </th>
                    <th>Name</th>
                    <th>Size</th>
                    <th>Changed</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((entry) =>
                    entry.kind === 'directory' ? (
                      <tr key={entry.name}>
                        <td />
                        <td colSpan={3}>
                          <button className="link" onClick={() => setPath(join(listing.path, entry.name))}>
                            {entry.name}/
                          </button>
                        </td>
                      </tr>
                    ) : (
                      <tr key={entry.name}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={entry.name}
                            checked={chosen.has(entry.name)}
                            onChange={() => toggle(entry.name)}
                          />
                        </td>
                        <td onClick={() => toggle(entry.name)}>{entry.name}</td>
                        <td className="number">{size(entry.size)}</td>
                        <td>{when(entry.modified)}</td>
                      </tr>
                    ),
                  )}
                  {files.length === 0 && (
                    <tr>
                      <td />
                      <td colSpan={3} className="muted">
                        No {all ? '' : 'output '}files here.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p className="muted small">
              One file opens the import dialog; several go through the batch import, which matches each to a node. Files
              are copied into the investigation; nothing changes on {server.name}.
            </p>
          </>
        )}
      </div>
    </Modal>
  )
}
