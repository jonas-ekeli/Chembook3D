// D85, D87: the notes on a node's card. A note sits on a corner of the card, stacked outward
// from it, or floats apart from it (joined to the corner by a line, or not) and moves with the
// card. An expanded note can be resized from its outer corner; a floating one is dragged by
// its head.

import {
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import { NOTE_CORNERS, type Note, type NoteCorner, type NoteLayout } from '../api'
import { NoteBody } from './NoteContent'
import { noteTitle } from '../notes'

/** What a note on the canvas can do; in the read-only copy nothing is saved. */
export type NoteActions = {
  onOpen?: (noteId: string) => void
  /** Saves how it is drawn: collapsed, its size, where it floats. */
  onLayout?: (noteId: string, layout: Partial<NoteLayout>) => void
}

// The limits of services/notes.py.
const MIN_WIDTH = 160
const MAX_WIDTH = 1000
const MIN_HEIGHT = 60
const MAX_HEIGHT = 1200
// How far a note moves away from the card when it is detached, and how far the pointer must
// move before dragging a note's head starts.
const DETACH_STEP = 24
const DRAG_START = 4

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

/** +1 where the corner's outward direction is right or down on the screen, −1 otherwise. */
function outward(corner: NoteCorner): [number, number] {
  return [corner.endsWith('right') ? 1 : -1, corner.startsWith('bottom') ? 1 : -1]
}

/** Follows the pointer from a press until it is released; deltas are in canvas px. */
function follow(
  event: ReactPointerEvent<HTMLElement>,
  onMove: (dx: number, dy: number) => void,
  onEnd: () => void,
) {
  const element = event.currentTarget
  const card = element.closest<HTMLElement>('.cnode')
  // The canvas is zoomed with a transform: screen px over layout px.
  const scale = card ? card.getBoundingClientRect().width / card.offsetWidth || 1 : 1
  const startX = event.clientX
  const startY = event.clientY
  element.setPointerCapture(event.pointerId)
  const move = (e: PointerEvent) => onMove((e.clientX - startX) / scale, (e.clientY - startY) / scale)
  const end = () => {
    element.removeEventListener('pointermove', move)
    element.removeEventListener('pointerup', end)
    element.removeEventListener('pointercancel', end)
    onEnd()
  }
  element.addEventListener('pointermove', move)
  element.addEventListener('pointerup', end)
  element.addEventListener('pointercancel', end)
}

/** Where a note on the corner is now, as the offset it would have floating (outward px). */
function offsetNow(element: HTMLElement, corner: NoteCorner): [number, number] {
  const card = element.closest<HTMLElement>('.cnode')
  if (!card) return [40, 40]
  const box = card.getBoundingClientRect()
  const note = element.getBoundingClientRect()
  const scale = box.width / card.offsetWidth || 1
  const [sx, sy] = outward(corner)
  // The note's corner facing the card's corner, and that corner of the card.
  const fx = sx > 0 ? note.left : note.right
  const fy = sy > 0 ? note.top : note.bottom
  const cx = sx > 0 ? box.right : box.left
  const cy = sy > 0 ? box.bottom : box.top
  return [Math.round(((fx - cx) * sx) / scale), Math.round(((fy - cy) * sy) / scale)]
}

function NoteCard({
  note,
  corner,
  floating,
  onToggle,
  onOpen,
  onLayout,
  onLive,
}: {
  note: Note
  corner: NoteCorner
  floating: boolean
  onToggle: () => void
  onOpen?: () => void
  /** Absent in the read-only copy: it cannot be moved or resized there. */
  onLayout?: (layout: Partial<NoteLayout>) => void
  /** While it is dragged or resized, how it is drawn. */
  onLive: (layout: Partial<NoteLayout> | null) => void
}) {
  const title = noteTitle(note)
  const collapsed = note.collapsed
  const [sx, sy] = outward(corner)

  const drag = (event: ReactPointerEvent<HTMLDivElement>) => {
    // A note on the corner is detached with its pin first.
    if (!onLayout || !floating || event.button !== 0 || (event.target as HTMLElement).closest('button')) return
    event.stopPropagation()
    const [ox, oy] = [note.offset_x, note.offset_y]
    const placement = note.placement
    let last: Partial<NoteLayout> | null = null
    follow(
      event,
      (dx, dy) => {
        if (!last && Math.hypot(dx, dy) < DRAG_START) return
        last = { placement, offset_x: Math.round(ox + dx * sx), offset_y: Math.round(oy + dy * sy) }
        onLive(last)
      },
      () => {
        if (last) onLayout(last)
        else onLive(null)
      },
    )
  }

  const resize = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!onLayout || event.button !== 0) return
    event.stopPropagation()
    const element = event.currentTarget.closest<HTMLElement>('.cnote')!
    const width = element.offsetWidth
    const height = element.offsetHeight
    let last: Partial<NoteLayout> | null = null
    follow(
      event,
      (dx, dy) => {
        last = {
          width: Math.round(clamp(width + dx * sx, MIN_WIDTH, MAX_WIDTH)),
          height: Math.round(clamp(height + dy * sy, MIN_HEIGHT, MAX_HEIGHT)),
        }
        onLive(last)
      },
      () => {
        if (last) onLayout(last)
        else onLive(null)
      },
    )
  }

  const detach = (event: ReactMouseEvent<HTMLButtonElement>) => {
    event.stopPropagation()
    if (!onLayout) return
    if (floating) {
      onLayout({ placement: 'corner' })
      return
    }
    const [ox, oy] = offsetNow(event.currentTarget.closest<HTMLElement>('.cnote')!, corner)
    onLayout({ placement: 'line', offset_x: ox + DETACH_STEP, offset_y: oy + DETACH_STEP })
  }

  const sized = !collapsed && note.height != null
  return (
    <div
      className={`cnote note-${note.colour}${collapsed ? ' collapsed' : ''}${sized ? ' sized' : ''}${
        onLayout && !collapsed ? ` resizable grip-${corner}` : ''
      }`}
      style={collapsed ? undefined : { width: note.width, height: note.height ?? undefined }}
      data-testid="canvas-note"
      role="note"
      aria-label={`Note ${title}`}
      title={onOpen ? 'Double-click to edit' : undefined}
      onDoubleClick={(event) => {
        if (!onOpen) return
        event.stopPropagation()
        onOpen()
      }}
    >
      <div
        className={`cnote-head${onLayout && floating ? ' movable' : ''}`}
        title={onLayout && floating ? 'Drag to move the note' : undefined}
        onPointerDown={drag}
      >
        {onLayout ? (
          <button
            className="small cnote-pin"
            aria-label={floating ? 'Put note back on its corner' : 'Detach note'}
            title={floating ? 'Put the note back on its corner' : 'Detach: let the note float, joined to its corner by a line'}
            onClick={detach}
            onDoubleClick={(event) => event.stopPropagation()}
          >
            {floating ? '📍' : '📌'}
          </button>
        ) : (
          <span className="cnote-pin" aria-hidden="true">
            {floating ? '📍' : '📌'}
          </span>
        )}
        <span className="cnote-title">{title}</span>
        <button
          className="small"
          aria-label={collapsed ? 'Expand note' : 'Collapse note'}
          onClick={(event) => {
            event.stopPropagation()
            onToggle()
          }}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          {collapsed ? '+' : '−'}
        </button>
      </div>
      {!collapsed && (
        <div className="cnote-body nowheel">
          <NoteBody html={note.body} />
        </div>
      )}
      {onLayout && !collapsed && (
        <div
          className="cnote-grip"
          role="separator"
          aria-label="Resize note"
          title="Drag to resize the note"
          onPointerDown={resize}
          onDoubleClick={(event) => event.stopPropagation()}
        />
      )}
    </div>
  )
}

/** A note floating apart from the card: placed from the card's corner, with its line. */
function FloatingNote({ note, children }: { note: Note; children: ReactNode }) {
  const [sx, sy] = outward(note.corner)
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState<[number, number]>([note.width, 40])
  useLayoutEffect(() => {
    const element = box.current
    if (!element) return
    const measure = () => setSize([element.offsetWidth, element.offsetHeight])
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  // The line ends at the point of the note nearest the card's corner.
  const [w, h] = size
  const ex = clamp(0, note.offset_x, note.offset_x + w) * sx
  const ey = clamp(0, note.offset_y, note.offset_y + h) * sy
  return (
    <div className={`cnote-anchor at-${note.corner} note-${note.colour} nodrag nopan`}>
      {note.placement === 'line' && (
        <svg className="cnote-tether" width="1" height="1" aria-hidden="true" data-testid="note-line">
          <line x1="0" y1="0" x2={ex} y2={ey} />
          <circle cx="0" cy="0" r="3" />
        </svg>
      )}
      <div
        ref={box}
        className="cnote-float"
        style={{
          [sx > 0 ? 'left' : 'right']: note.offset_x,
          [sy > 0 ? 'top' : 'bottom']: note.offset_y,
        }}
      >
        {children}
      </div>
    </div>
  )
}

/** A node's notes: those on a corner stacked outward from it, the others floating. */
export function CardNotes({ notes, actions }: { notes: Note[]; actions: NoteActions }) {
  // Changes show at once; the saved notes replace these when they come back.
  const [shown, setShown] = useState<Record<string, Partial<NoteLayout>>>({})
  const [live, setLive] = useState<{ id: string; layout: Partial<NoteLayout> } | null>(null)
  const [seen, setSeen] = useState(notes)
  if (seen !== notes) {
    setSeen(notes)
    setShown({})
  }
  if (!notes.length) return null
  const drawn = notes.map((note) => ({
    ...note,
    ...shown[note.id],
    ...(live?.id === note.id ? live.layout : null),
  }))
  const card = (note: Note) => {
    const save = (layout: Partial<NoteLayout>) => {
      setShown((current) => ({ ...current, [note.id]: { ...current[note.id], ...layout } }))
      setLive(null)
      actions.onLayout?.(note.id, layout)
    }
    return (
      <NoteCard
        key={note.id}
        note={note}
        corner={note.corner}
        floating={note.placement !== 'corner'}
        // Collapsing works in the read-only copy too, without saving anything.
        onToggle={() =>
          actions.onLayout
            ? save({ collapsed: !note.collapsed })
            : setShown((current) => ({ ...current, [note.id]: { collapsed: !note.collapsed } }))
        }
        onOpen={actions.onOpen ? () => actions.onOpen!(note.id) : undefined}
        onLayout={actions.onLayout ? save : undefined}
        onLive={(layout) => setLive(layout ? { id: note.id, layout } : null)}
      />
    )
  }
  return (
    <>
      {NOTE_CORNERS.map((corner) => {
        const here = drawn.filter((n) => n.corner === corner && n.placement === 'corner')
        if (!here.length) return null
        return (
          <div key={corner} className={`cnotes cnotes-${corner} nodrag nopan`}>
            {here.map(card)}
          </div>
        )
      })}
      {drawn
        .filter((n) => n.placement !== 'corner')
        .map((note) => (
          <FloatingNote key={note.id} note={note}>
            {card(note)}
          </FloatingNote>
        ))}
    </>
  )
}
