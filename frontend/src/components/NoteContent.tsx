// D85: a note pinned to a node's card, drawn from its cleaned HTML (see ../notes.ts) as React
// elements, never by inserting HTML.

import { createElement, useMemo, type ReactNode } from 'react'
import type { Note } from '../api'
import { ALLOWED, attributes, noteTitle, parse, SKIPPED, useNoteImage, VOID } from '../notes'

function toReact(node: ChildNode, key: number, image: (id: string) => string | undefined): ReactNode {
  if (node.nodeType === 3) return node.textContent
  if (!(node instanceof Element)) return null
  const tag = node.tagName.toLowerCase()
  const children = () => Array.from(node.childNodes).map((child, i) => toReact(child, i, image))
  if (SKIPPED.has(tag)) return null
  if (!(tag in ALLOWED)) return createElement('span', { key }, ...children())
  const kept = Object.fromEntries(attributes(node, tag))
  if (tag === 'img') {
    const id = kept['data-note-image']
    const src = id ? image(id) : undefined
    if (!src) return <span key={key} className="muted">[picture missing]</span>
    return <img key={key} src={src} alt={kept.alt ?? ''} draggable={false} />
  }
  if (tag === 'a') {
    return kept.href ? (
      <a key={key} href={kept.href} target="_blank" rel="noreferrer noopener">
        {children()}
      </a>
    ) : (
      <span key={key}>{children()}</span>
    )
  }
  if (VOID.has(tag)) return createElement(tag, { key })
  return createElement(tag, { key }, ...children())
}

/** A note's text and pictures, drawn from the cleaned HTML. */
export function NoteBody({ html }: { html: string }) {
  const image = useNoteImage()
  const shown = useMemo(() => Array.from(parse(html).body.childNodes).map((node, i) => toReact(node, i, image)), [html, image])
  return <div className="note-body">{shown}</div>
}

const CORNER_LABEL: Record<string, string> = {
  'top-left': 'top left',
  'top-right': 'top right',
  'bottom-left': 'bottom left',
  'bottom-right': 'bottom right',
}

/** The notes pinned to a node's card, read in full in the side panel (D85). */
export function PinnedNotesSection({
  notes,
  onAdd,
  onEdit,
}: {
  notes: Note[]
  onAdd?: () => void
  onEdit?: (noteId: string) => void
}) {
  if (!notes.length && !onAdd) return null
  return (
    <section aria-label="Pinned notes">
      <div className="section-head">
        <h3>Pinned notes</h3>
        {onAdd && (
          <button title="A note pinned to a corner of this node's card on the canvas" onClick={onAdd}>
            Add pinned note
          </button>
        )}
      </div>
      {notes.length === 0 ? (
        <p className="muted">None. A pinned note shows on a corner of the card, with text and pictures.</p>
      ) : (
        <ul className="plain pinned-notes">
          {notes.map((note) => (
            <li key={note.id} className={`pinned-note note-${note.colour}`}>
              <div className="pinned-note-head">
                <strong>{noteTitle(note)}</strong>
                <span className="muted small">
                  {CORNER_LABEL[note.corner] ?? note.corner}
                  {note.placement !== 'corner' && ', floating'}
                </span>
                {onEdit && (
                  <button className="small" onClick={() => onEdit(note.id)}>
                    Edit…
                  </button>
                )}
              </div>
              <NoteBody html={note.body} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
