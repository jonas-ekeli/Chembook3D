import { useState } from 'react'
import { api, ApiError, type Node, type XyzLineError } from '../api'

/** Coordinates as xyz text (FR-NODE-03). Saving follows the identity rules in the backend:
 * in place while the node has no calculations (ID-4), otherwise a new derived node (ID-5).
 * Copy and Save .xyz give the stored coordinates (FR-3D-06). Remount with a new key when the
 * stored coordinates change. */
export function XyzEditor({
  node,
  onSaved,
}: {
  node: Node
  onSaved: (node: Node, derived: boolean) => void
}) {
  const stored = node.xyz ?? ''
  const [text, setText] = useState(stored)
  const [errors, setErrors] = useState<XyzLineError[]>([])
  const [message, setMessage] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const dirty = text !== stored
  const derives = node.calculation_count > 0

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
      {derives && (
        <p className="muted">
          This node has {node.calculation_count} calculation
          {node.calculation_count === 1 ? '' : 's'}, so saving keeps it unchanged and creates a new
          node derived from it.
        </p>
      )}
      <div className="buttons">
        <button className="primary" disabled={!dirty || saving || !text.trim()} onClick={save}>
          {derives ? 'Save as derived node' : 'Save coordinates'}
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
    </section>
  )
}
