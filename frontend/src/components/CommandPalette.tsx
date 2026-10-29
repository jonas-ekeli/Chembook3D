import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { fuzzy, PALETTE_KEY, SHORTCUTS, type PaletteEntry, type PaletteGroup } from '../commands'
import { Icon } from '../icons'
import { Modal } from './Modal'

/** How many of each group are listed: with nothing typed, and with a query. */
const SHOWN: Record<PaletteGroup, [number, number]> = {
  Commands: [12, 12],
  Nodes: [6, 10],
  'Sub-investigations': [4, 6],
  Branches: [4, 6],
  Steps: [4, 6],
}
const ORDER: PaletteGroup[] = ['Nodes', 'Sub-investigations', 'Branches', 'Steps', 'Commands']

type Hit = { entry: PaletteEntry; score: number; marks: number[] }

/** A label with the letters the query found in it marked. */
function Marked({ text, marks }: { text: string; marks: number[] }) {
  if (!marks.length) return <>{text}</>
  const parts: ReactNode[] = []
  const set = new Set(marks)
  let i = 0
  while (i < text.length) {
    const marked = set.has(i)
    let j = i
    while (j < text.length && set.has(j) === marked) j++
    parts.push(marked ? <mark key={i}>{text.slice(i, j)}</mark> : text.slice(i, j))
    i = j
  }
  return <>{parts}</>
}

/**
 * D184: the command palette (Ctrl K, ⌘ K on a Mac). A search over the nodes (label, step, branch,
 * with ΔX, where it is), sub-investigations to open (D193), branches, steps and commands (the app bar's and the investigation menu's, the views,
 * filters, energies, settings sections, and what the selection's panel offers). With nothing
 * typed the commands come first, those for the selection at the top; typed words are found as
 * runs anywhere or as the starts of words, and the groups follow their best match. ↑ and ↓
 * move, Enter runs the command or selects and centres the node, Escape closes.
 */
export function CommandPalette({
  build,
  onClose,
  onShortcuts,
}: {
  /** The entries, read once as the palette opens. */
  build: () => PaletteEntry[]
  onClose: () => void
  onShortcuts: () => void
}) {
  const [entries] = useState(build)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const groups = useMemo(() => {
    const typed = query.trim().length > 0
    const found = new Map<PaletteGroup, Hit[]>()
    entries.forEach((entry, index) => {
      const own = fuzzy(query, labelOf(entry))
      // Found by its other words (a node's step or branch): listed, below those found by name.
      const other = own ? null : entry.keywords ? fuzzy(query, `${entry.label} ${entry.keywords}`) : null
      const hit = own ?? (other && { score: other.score / 2, marks: [] })
      if (!hit) return
      const list = found.get(entry.group) ?? []
      // Ties keep the order given.
      list.push({ entry, score: hit.score - index * 1e-6, marks: own ? own.marks : [] })
      found.set(entry.group, list)
    })
    const order = typed
      ? [...ORDER].sort((a, b) => best(found.get(b)) - best(found.get(a)))
      : ['Commands' as PaletteGroup, ...ORDER.filter((g) => g !== 'Commands')]
    return order
      .map((group) => ({
        group,
        hits: (found.get(group) ?? [])
          .sort((a, b) => (typed ? b.score - a.score : 0))
          .slice(0, SHOWN[group][typed ? 1 : 0]),
      }))
      .filter((g) => g.hits.length > 0)
  }, [entries, query])
  const flat = groups.flatMap((g) => g.hits)
  // Each entry's place in the whole list, for ↑ and ↓.
  const firstOf = groups.map((_, i) => groups.slice(0, i).reduce((sum, g) => sum + g.hits.length, 0))
  const current = Math.min(active, Math.max(0, flat.length - 1))

  useEffect(() => {
    input.current?.focus()
  }, [])
  useLayoutEffect(() => {
    listRef.current?.querySelector(`[data-at="${current}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [current])

  const choose = (hit: Hit | undefined) => {
    if (!hit) return
    onClose()
    hit.entry.run()
  }

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="palette-query">
          <Icon name="search" />
          <input
            ref={input}
            role="combobox"
            aria-label="Search nodes and commands"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={flat.length ? `palette-${current}` : undefined}
            aria-autocomplete="list"
            placeholder="Search nodes, branches, steps and commands"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setActive(0)
            }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault()
                if (!flat.length) return
                const step = event.key === 'ArrowDown' ? 1 : -1
                setActive((current + step + flat.length) % flat.length)
              } else if (event.key === 'Enter') {
                event.preventDefault()
                choose(flat[current])
              } else if (event.key === 'Escape') {
                event.preventDefault()
                event.stopPropagation()
                onClose()
              }
            }}
          />
        </div>
        <div className="palette-list" id="palette-list" role="listbox" aria-label="Results" ref={listRef}>
          {groups.map(({ group, hits }, g) => (
            <div key={group} role="group" aria-label={group}>
              <div className="palette-group" aria-hidden="true">
                {group}
              </div>
              {hits.map((hit, h) => {
                const index = firstOf[g] + h
                const { entry } = hit
                return (
                  <div
                    key={entry.id}
                    id={`palette-${index}`}
                    data-at={index}
                    role="option"
                    aria-selected={index === current}
                    className="palette-item"
                    onMouseMove={() => index !== current && setActive(index)}
                    onClick={() => choose(hit)}
                  >
                    {entry.colour ? (
                      <span className="swatch" style={{ background: entry.colour }} aria-hidden="true" />
                    ) : (
                      <Icon name={entry.icon} />
                    )}
                    <span className="palette-label">
                      {entry.ts && (
                        <span className={`ts-mark${hit.marks.some((m) => m < 2) ? ' marked' : ''}`}>TS</span>
                      )}
                      <span className="palette-text">
                        <Marked text={entry.label} marks={labelMarks(entry, hit.marks)} />
                      </span>
                    </span>
                    {entry.detail && <span className="palette-detail">{entry.detail}</span>}
                    {index === current ? (
                      <kbd className="palette-key">Enter</kbd>
                    ) : (
                      entry.shortcut && <kbd className="palette-key">{entry.shortcut}</kbd>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
          {flat.length === 0 && <p className="palette-empty muted">Nothing matches “{query.trim()}”.</p>}
        </div>
        <div className="palette-foot">
          <span>↑↓ to move</span>
          <span>Enter to open</span>
          <span>{PALETTE_KEY} anywhere</span>
          <span className="spacer" />
          <button
            className="link"
            onClick={() => {
              onClose()
              onShortcuts()
            }}
          >
            Shortcuts: ?
          </button>
        </div>
      </div>
    </div>
  )
}

/** What is searched by name: a TS node's label after its mark. */
const labelOf = (entry: PaletteEntry) => (entry.ts ? `TS ${entry.label}` : entry.label)
/** The marks that fall in the label itself, counted from its start. */
const labelMarks = (entry: PaletteEntry, marks: number[]) =>
  entry.ts ? marks.filter((m) => m > 2).map((m) => m - 3) : marks

function best(hits: Hit[] | undefined): number {
  return hits?.length ? Math.max(...hits.map((h) => h.score)) : -Infinity
}

/** D184: the keyboard shortcuts, opened by "?" on the canvas and from the palette. */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="Keyboard shortcuts" onClose={onClose} actions={<button onClick={onClose}>Close</button>}>
      <p className="muted small">
        The single keys work while the canvas has the focus (click a node or an empty spot first), not while you type
        in a field.
      </p>
      <table className="shortcuts">
        <tbody>
          {SHORTCUTS.map((s) => (
            <tr key={s.keys}>
              <td>
                <kbd>{s.keys}</kbd>
              </td>
              <td>{s.what}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Modal>
  )
}

/** Hands every key press in the window to the latest `onKey` (D184). */
export function KeyListener({ onKey }: { onKey: (event: KeyboardEvent) => void }) {
  const latest = useRef(onKey)
  useLayoutEffect(() => {
    latest.current = onKey
  })
  useEffect(() => {
    const listener = (event: KeyboardEvent) => latest.current(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])
  return null
}
