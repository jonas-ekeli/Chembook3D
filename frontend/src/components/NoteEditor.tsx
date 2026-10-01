import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent } from 'react'
import {
  api,
  NOTE_COLOURS,
  NOTE_CORNER_LABEL,
  NOTE_CORNERS,
  NOTE_PLACEMENT_LABEL,
  NOTE_PLACEMENTS,
  type Note,
  type NoteColour,
  type NoteCorner,
  type NotePlacement,
} from '../api'
import { Modal } from './Modal'
import { cleanNoteHtml } from '../notes'

const WIDTHS: { value: number; label: string }[] = [
  { value: 200, label: 'Narrow' },
  { value: 260, label: 'Medium' },
  { value: 380, label: 'Wide' },
  { value: 520, label: 'Extra wide' },
]

const COLOUR_LABEL: Record<NoteColour, string> = {
  yellow: 'Yellow',
  blue: 'Blue',
  green: 'Green',
  pink: 'Pink',
  grey: 'Grey',
}

const PICTURE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml']
const isPicture = (file: File) => PICTURE_TYPES.includes(file.type) || /\.svg$/i.test(file.name)
const looksLikeSvg = (text: string) => /^\s*(<\?xml[^>]*>\s*)?(<!--[\s\S]*?-->\s*)*(<!DOCTYPE[^>]*>\s*)?<svg[\s>]/i.test(text)

/** What to do when the clipboard holds nothing a web page can read (D85). Copying in ChemDraw
 * puts its own drawing format and a Windows metafile there, which browsers do not pass on. */
const NOTHING_TO_PASTE =
  'The clipboard holds nothing this page can read. ChemDraw’s own copy is not passed on by browsers: ' +
  'in ChemDraw use Edit › Copy As › PNG and paste again, or File › Save As › SVG and drop the .svg file here.'
const CDXML_PASTE =
  'This is ChemDraw’s CDXML text, which cannot be drawn here. In ChemDraw use Edit › Copy As › PNG, ' +
  'or File › Save As › SVG and drop the .svg file here.'

type Tool = { label: string; title: string; command: string; className?: string }
const TOOLS: Tool[] = [
  { label: 'B', title: 'Bold', command: 'bold', className: 'tool-bold' },
  { label: 'I', title: 'Italic', command: 'italic', className: 'tool-italic' },
  { label: 'U', title: 'Underline', command: 'underline', className: 'tool-underline' },
  { label: 'S', title: 'Strikethrough', command: 'strikeThrough', className: 'tool-strike' },
  { label: 'x₂', title: 'Subscript', command: 'subscript' },
  { label: 'x²', title: 'Superscript', command: 'superscript' },
  { label: '• List', title: 'Bulleted list', command: 'insertUnorderedList' },
  { label: '1. List', title: 'Numbered list', command: 'insertOrderedList' },
  { label: 'Clear', title: 'Clear formatting', command: 'removeFormat' },
]

/** Write a note pinned to a node's card (D85): formatted text, and pictures pasted, dropped or
 * chosen. Pictures are stored as soon as they arrive; the text when the note is saved. */
export function NoteEditor({
  note,
  nodeId,
  nodeLabel,
  onClose,
  onSaved,
}: {
  /** The note to edit, or null for a new one on `nodeId`. */
  note: Note | null
  nodeId: string
  nodeLabel: string
  onClose: () => void
  onSaved: () => void
}) {
  const editor = useRef<HTMLDivElement>(null)
  const [title, setTitle] = useState(note?.title ?? '')
  const [corner, setCorner] = useState<NoteCorner>(note?.corner ?? 'top-right')
  const [colour, setColour] = useState<NoteColour>(note?.colour ?? 'yellow')
  const [width, setWidth] = useState(note?.width ?? 260)
  const [placement, setPlacement] = useState<NotePlacement>(note?.placement ?? 'corner')
  const [height, setHeight] = useState<number | null>(note?.height ?? null)
  const [hint, setHint] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(0)
  const [saving, setSaving] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [dirty, setDirty] = useState(false)

  // Filled once: a canvas refresh while the dialog is open must not undo what was typed.
  const initial = useRef(note?.body ?? '')
  useEffect(() => {
    const element = editor.current
    if (!element) return
    // The stored text is already clean; cleaning it again keeps only the allowed tags.
    element.innerHTML = cleanNoteHtml(initial.current)
    for (const img of element.querySelectorAll<HTMLImageElement>('img[data-note-image]')) {
      img.src = `/api/note-images/${img.dataset.noteImage}`
    }
    element.focus()
  }, [])

  const markDirty = () => setDirty(true)

  const run = (command: string, value?: string) => {
    editor.current?.focus()
    document.execCommand(command, false, value)
    markDirty()
  }

  /** Where a picture goes: the caret if it is in the note, else the end. */
  const insertionPoint = (): Range => {
    const element = editor.current!
    const selection = window.getSelection()
    if (selection && selection.rangeCount && element.contains(selection.getRangeAt(0).commonAncestorContainer)) {
      return selection.getRangeAt(0)
    }
    const range = document.createRange()
    range.selectNodeContents(element)
    range.collapse(false)
    return range
  }

  const insertPicture = (id: string, range: Range) => {
    const img = document.createElement('img')
    img.dataset.noteImage = id
    img.alt = ''
    img.src = `/api/note-images/${id}`
    range.deleteContents()
    range.insertNode(img)
    range.setStartAfter(img)
    range.collapse(true)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    markDirty()
  }

  const addPictures = async (pictures: Blob[], range: Range = insertionPoint()) => {
    setError(null)
    setHint(null)
    setUploading((n) => n + pictures.length)
    // Keep the place while uploading; typing meanwhile moves the caret, not the picture.
    const marker = document.createElement('span')
    range.insertNode(marker)
    try {
      for (const picture of pictures) {
        const stored = await api.uploadNoteImage(picture)
        const at = document.createRange()
        at.setStartBefore(marker)
        at.collapse(true)
        insertPicture(stored.id, at)
      }
    } catch (err) {
      setError(`The picture could not be added: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      marker.remove()
      setUploading((n) => n - pictures.length)
    }
  }

  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const data = event.clipboardData
    const files = Array.from(data.files).filter(isPicture)
    const text = data.getData('text/plain')
    const html = data.getData('text/html')
    setHint(null)
    if (files.length && !text.trim()) {
      event.preventDefault()
      void addPictures(files)
      return
    }
    if (looksLikeSvg(text)) {
      // SVG markup copied as text, e.g. from an editor or a web page's source.
      event.preventDefault()
      void addPictures([new Blob([text], { type: 'image/svg+xml' })])
      return
    }
    if (/<CDXML[\s>]/.test(text)) {
      event.preventDefault()
      setHint(CDXML_PASTE)
      return
    }
    if (html.trim()) {
      event.preventDefault()
      document.execCommand('insertHTML', false, cleanNoteHtml(html))
      markDirty()
      return
    }
    if (text) {
      event.preventDefault()
      document.execCommand('insertText', false, text)
      markDirty()
      return
    }
    event.preventDefault()
    setHint(NOTHING_TO_PASTE)
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    const files = Array.from(event.dataTransfer.files)
    if (!files.length) return
    event.preventDefault()
    const pictures = files.filter(isPicture)
    if (!pictures.length) {
      setHint('Only PNG, JPEG, GIF, WebP and SVG pictures can go in a note.')
      return
    }
    const doc = document as Document & {
      caretRangeFromPoint?: (x: number, y: number) => Range | null
      caretPositionFromPoint?: (x: number, y: number) => { offsetNode: globalThis.Node; offset: number } | null
    }
    let range = doc.caretRangeFromPoint?.(event.clientX, event.clientY) ?? null
    if (!range && doc.caretPositionFromPoint) {
      const position = doc.caretPositionFromPoint(event.clientX, event.clientY)
      if (position) {
        range = document.createRange()
        range.setStart(position.offsetNode, position.offset)
        range.collapse(true)
      }
    }
    const inside = range && editor.current?.contains(range.commonAncestorContainer)
    void addPictures(pictures, inside ? range! : insertionPoint())
  }

  const close = () => {
    if (dirty && !window.confirm('Discard the changes to this note?')) return
    onClose()
  }

  const save = async () => {
    const body = cleanNoteHtml(editor.current?.innerHTML ?? '')
    const fields = { title, corner, placement, colour, width, height, body }
    setSaving(true)
    setError(null)
    try {
      if (note) await api.updateNote(note.id, fields)
      else await api.createNote(nodeId, fields)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }

  const remove = async () => {
    if (!note) return
    try {
      await api.deleteNote(note.id)
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Modal
      title={note ? `Note on ${nodeLabel}` : `New note on ${nodeLabel}`}
      wide
      onClose={close}
      actions={
        <>
          {note &&
            (confirmDelete ? (
              <button className="danger" onClick={remove}>
                Delete this note
              </button>
            ) : (
              <button onClick={() => setConfirmDelete(true)}>Delete…</button>
            ))}
          <span className="spacer" />
          <button onClick={close}>Cancel</button>
          <button className="primary" disabled={saving || uploading > 0} onClick={save}>
            {uploading > 0 ? 'Adding picture…' : 'Save note'}
          </button>
        </>
      }
    >
      <div className="note-settings">
        <label className="field">
          <span>Title</span>
          <input
            value={title}
            placeholder="Shown when the note is collapsed"
            onChange={(event) => {
              setTitle(event.target.value)
              markDirty()
            }}
          />
        </label>
        <label className="field">
          <span>Corner</span>
          <select
            value={corner}
            onChange={(event) => {
              setCorner(event.target.value as NoteCorner)
              markDirty()
            }}
          >
            {NOTE_CORNERS.map((c) => (
              <option key={c} value={c}>
                {NOTE_CORNER_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Placement</span>
          <select
            value={placement}
            title="A floating note can be dragged by its head; it moves with the card"
            onChange={(event) => {
              setPlacement(event.target.value as NotePlacement)
              markDirty()
            }}
          >
            {NOTE_PLACEMENTS.map((p) => (
              <option key={p} value={p}>
                {NOTE_PLACEMENT_LABEL[p]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Width</span>
          <select
            value={width}
            onChange={(event) => {
              setWidth(Number(event.target.value))
              markDirty()
            }}
          >
            {WIDTHS.some((w) => w.value === width) ? null : <option value={width}>{width} px</option>}
            {WIDTHS.map((w) => (
              <option key={w.value} value={w.value}>
                {w.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Height</span>
          <select
            value={height ?? ''}
            title="Drag the note's outer corner on the canvas to resize it"
            onChange={(event) => {
              setHeight(event.target.value ? Number(event.target.value) : null)
              markDirty()
            }}
          >
            <option value="">Fits the text</option>
            {height != null && <option value={height}>{height} px</option>}
          </select>
        </label>
        <div className="field">
          <span>Colour</span>
          <span className="note-swatches" role="radiogroup" aria-label="Colour">
            {NOTE_COLOURS.map((c) => (
              <button
                key={c}
                role="radio"
                aria-checked={colour === c}
                aria-label={COLOUR_LABEL[c]}
                title={COLOUR_LABEL[c]}
                className={`note-swatch note-${c}`}
                onClick={() => {
                  setColour(c)
                  markDirty()
                }}
              />
            ))}
          </span>
        </div>
      </div>
      <div className="note-toolbar" role="toolbar" aria-label="Formatting">
        {TOOLS.map((tool) => (
          <button
            key={tool.command}
            className={`small ${tool.className ?? ''}`}
            title={tool.title}
            aria-label={tool.title}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => run(tool.command)}
          >
            {tool.label}
          </button>
        ))}
        <button
          className="small"
          title="Link"
          aria-label="Link"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            const url = window.prompt('Link address (https://…)')
            if (url && /^(https?:|mailto:)/i.test(url.trim())) run('createLink', url.trim())
          }}
        >
          Link
        </button>
        <label className="small button-like" title="Insert a PNG, JPEG, GIF, WebP or SVG picture">
          Insert picture…
          <input
            type="file"
            hidden
            aria-label="Insert picture"
            accept={[...PICTURE_TYPES, '.svg'].join(',')}
            multiple
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []).filter(isPicture)
              event.target.value = ''
              if (files.length) void addPictures(files)
            }}
          />
        </label>
      </div>
      <div
        ref={editor}
        className={`note-editor note-${colour}`}
        role="textbox"
        aria-multiline="true"
        aria-label="Note text"
        contentEditable
        suppressContentEditableWarning
        onInput={markDirty}
        onPaste={onPaste}
        onDragOver={(event) => {
          if (event.dataTransfer.types.includes('Files')) event.preventDefault()
        }}
        onDrop={onDrop}
      />
      <p className="muted small">
        Paste or drop pictures here. From ChemDraw: File › Save As › SVG and drop the .svg file (stays sharp), or Edit ›
        Copy As › PNG and paste.
      </p>
      {hint && (
        <p className="notice" role="status">
          {hint}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Modal>
  )
}
