import { useState } from 'react'
import { api, ApiError, type Node, type RemoteServer, type XyzLineError } from '../api'
import { Modal } from './Modal'

/** Coordinates as xyz text (FR-NODE-03). Saving follows the identity rules in the backend:
 * in place while the node has no calculations (ID-4), otherwise a new derived node (ID-5).
 * Saving empty text removes the coordinates, only while there are no calculations (D90).
 * Copy and Save .xyz give the stored coordinates (FR-3D-06). Remount with a new key when the
 * stored coordinates change. "Send to <server>" writes the same file into the directory a
 * logged-in server's terminal is in, asking before it replaces one (D122f). */
export function XyzEditor({
  node,
  servers = [],
  onSaved,
}: {
  node: Node
  /** Servers logged in to, each with a terminal (D122). */
  servers?: RemoteServer[]
  onSaved: (node: Node, derived: boolean) => void
}) {
  const stored = node.xyz ?? ''
  const [text, setText] = useState(stored)
  const [errors, setErrors] = useState<XyzLineError[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [replacing, setReplacing] = useState<{ server: RemoteServer; path: string } | null>(null)

  const dirty = text !== stored
  const derives = node.calculation_count > 0
  const clearing = !text.trim() && !!node.xyz

  const save = async () => {
    setSaving(true)
    setMessage(null)
    try {
      const result = await api.setGeometry(node.id, text)
      setErrors([])
      onSaved(result.node, result.derived)
    } catch (err) {
      if (err instanceof ApiError && err.xyzErrors) setErrors(err.xyzErrors)
      else setMessage(String(err instanceof Error ? err.message : err))
    } finally {
      setSaving(false)
    }
  }

  const send = async (server: RemoteServer, replace = false) => {
    setReplacing(null)
    setMessage(null)
    try {
      const result = await api.remoteSend(server.id, node.id, replace)
      if (!result.sent) setReplacing({ server, path: result.path })
      else setMessage(`${result.exists ? 'Replaced' : 'Wrote'} ${result.path} on ${server.name}`)
    } catch (err) {
      setMessage(String(err instanceof ApiError && typeof err.detail === 'string' ? err.detail : err))
    }
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(stored)
      setMessage('Copied to the clipboard')
    } catch {
      setMessage('The browser did not allow copying; select the text and copy it instead')
    }
  }

  return (
    <section className="xyz" aria-label="Coordinates">
      <div className="section-head">
        <h3>Coordinates (xyz, Å)</h3>
        <div className="buttons">
          <button onClick={copy} disabled={!node.xyz}>
            Copy
          </button>
          <a
            className={`button${node.xyz ? '' : ' disabled'}`}
            href={node.xyz ? api.xyzDownloadUrl(node.id) : undefined}
            download
          >
            Save .xyz
          </a>
          {servers.map((server) => (
            <button
              key={server.id}
              disabled={!node.xyz || !server.cwd}
              title={
                server.cwd
                  ? `Write the saved coordinates as an .xyz file into ${server.cwd} on ${server.name}`
                  : `The ${server.name} terminal has not reported its directory`
              }
              onClick={() => void send(server)}
            >
              Send to {server.name}
            </button>
          ))}
        </div>
      </div>
      <textarea
        aria-label="xyz text"
        className="mono"
        spellCheck={false}
        rows={10}
        value={text}
        placeholder={'Paste xyz here: one atom per line, e.g.\nO  0.000  0.000  0.117'}
        onChange={(event) => setText(event.target.value)}
      />
      {errors.length > 0 && (
        <ul className="errors" role="alert" aria-label="xyz errors">
          {errors.map((error) => (
            <li key={`${error.line}-${error.message}`}>
              Line {error.line}: {error.message}
            </li>
          ))}
        </ul>
      )}
      {derives && clearing && (
        <p className="muted" role="note">
          The coordinates of a node with calculations cannot be removed: they are the geometry
          its calculations were run on.
        </p>
      )}
      {derives && !clearing && (
        <p className="muted">
          This node has {node.calculation_count} calculation
          {node.calculation_count === 1 ? '' : 's'}, so saving keeps it unchanged and creates a new
          node derived from it.
        </p>
      )}
      <div className="buttons">
        <button
          className="primary"
          disabled={!dirty || saving || (!text.trim() && (derives || !clearing))}
          onClick={save}
        >
          {clearing && !derives
            ? 'Remove coordinates'
            : derives
              ? 'Save as derived node'
              : 'Save coordinates'}
        </button>
        {dirty && (
          <button
            onClick={() => {
              setText(stored)
              setErrors([])
            }}
          >
            Discard changes
          </button>
        )}
        {message && <span className="muted">{message}</span>}
      </div>
      {replacing && (
        <Modal
          title={`Replace the file on ${replacing.server.name}?`}
          onClose={() => setReplacing(null)}
          actions={
            <>
              <button onClick={() => setReplacing(null)}>Keep it</button>
              <button className="primary" onClick={() => void send(replacing.server, true)}>
                Replace
              </button>
            </>
          }
        >
          <p>
            <code>{replacing.path}</code> is already there. Replace it with the coordinates of this node?
          </p>
        </Modal>
      )}
    </section>
  )
}
