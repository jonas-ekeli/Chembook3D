import { useState } from 'react'
import Markdown from 'react-markdown'

/** A text input that saves on Enter or when it loses focus. */
export function TextField({
  label,
  value,
  onCommit,
}: {
  label: string
  value: string
  onCommit: (value: string) => void
}) {
  // Parents remount this field with key={value} when the stored value changes.
  const [draft, setDraft] = useState(value)
  const commit = () => {
    if (draft !== value) onCommit(draft)
  }
  return (
    <label className="field">
      <span>{label}</span>
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
      />
    </label>
  )
}

export function Notes({ notes, onSave }: { notes: string; onSave: (notes: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(notes)
  return (
    <section aria-label="Notes">
      <div className="section-head">
        <h3>Notes</h3>
        {!editing && <button onClick={() => setEditing(true)}>Edit notes</button>}
      </div>
      {editing ? (
        <>
          <textarea
            aria-label="Notes text"
            rows={6}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Free text; Markdown is rendered"
          />
          <div className="buttons">
            <button
              className="primary"
              onClick={() => {
                if (draft !== notes) onSave(draft)
                setEditing(false)
              }}
            >
              Save notes
            </button>
            <button
              onClick={() => {
                setDraft(notes)
                setEditing(false)
              }}
            >
              Cancel
            </button>
          </div>
        </>
      ) : notes.trim() ? (
        <div className="markdown">
          <Markdown>{notes}</Markdown>
        </div>
      ) : (
        <p className="muted">No notes.</p>
      )}
    </section>
  )
}
