import { useEffect, useMemo, useState } from 'react'
import {
  api,
  ApiError,
  geometryToXyz,
  type BuilderDescription,
  type BuilderOptions,
  type BuilderParts,
  type BuilderStructure,
  type BuilderTool,
  type GeometryRows,
  type Node,
} from '../api'
import { parseXyz } from '../chem'
import { HydrogenDisplay } from '../display'
import { Modal } from './Modal'
import { Viewer3D, type AtomMarks } from './Viewer3D'

const ADDED_BOND = '#2f9e44'
const REMOVED_BOND = '#e03131'

function rowsOf(text: string | null): GeometryRows {
  return text ? parseXyz(text).map((a) => [a.element, a.x, a.y, a.z]) : []
}

function errorText(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === 'string') return err.detail
  return err instanceof Error ? err.message : String(err)
}

/** Atom numbers typed from 1, as picks from 0; null when they cannot be read. */
function typedPicks(text: string, count: number): number[] | null {
  const numbers = text
    .split(/[\s,;–-]+/)
    .filter(Boolean)
    .map((t) => Number(t))
  if (numbers.length > 4 || !numbers.every((n) => Number.isInteger(n) && n >= 1 && n <= count)) return null
  if (new Set(numbers).size !== numbers.length) return null
  return numbers.map((n) => n - 1)
}

const UNITS = { distance: 'Å', angle: '°', dihedral: '°' } as const

/** D139: change a node's structure in 3D and save it through the node's own rules (in place
 * without calculations, else a derived node; or always a new node). Atoms are picked by
 * clicking them or typing their numbers; the tools offered follow how many are picked. Every
 * change can be undone until the window is closed; nothing is stored before saving. */
export function StructureEditor({
  node,
  nodes,
  onClose,
  onSaved,
}: {
  node: Node
  /** Nodes whose structures can be put beside this one. */
  nodes: Node[]
  onClose: () => void
  onSaved: (node: Node, derived: boolean) => void
}) {
  const start: BuilderStructure = useMemo(() => ({ rows: rowsOf(node.xyz), added: [], removed: [] }), [node.xyz])
  const [structure, setStructure] = useState<BuilderStructure>(start)
  const [past, setPast] = useState<BuilderStructure[]>([])
  const [future, setFuture] = useState<BuilderStructure[]>([])
  const [picks, setPicks] = useState<number[]>([])
  const [typed, setTyped] = useState('')
  const [described, setDescribed] = useState<BuilderDescription | null>(null)
  const [parts, setParts] = useState<BuilderParts | null>(null)
  const [value, setValue] = useState('')
  const [turn, setTurn] = useState('120')
  const [move, setMove] = useState<number | null>(null)
  const [element, setElement] = useState('C')
  const [added, setAdded] = useState('H')
  const [fragment, setFragment] = useState('Me')
  const [molecule, setMolecule] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [closing, setClosing] = useState(false)

  const changed = past.length > 0
  const count = structure.rows.length
  const xyz = count ? geometryToXyz(structure.rows) : ''
  const hasCalculations = node.calculation_count > 0
  const others = nodes.filter((n) => n.xyz)

  useEffect(() => {
    api.builderParts().then(setParts, (err: unknown) => setError(errorText(err)))
  }, [])

  useEffect(() => {
    let current = true
    api.builderDescribe(structure, picks, node.charge, node.multiplicity).then(
      (d) => {
        if (!current) return
        setDescribed(d)
        setValue(d.value === null ? '' : d.kind === 'distance' ? d.value.toFixed(3) : d.value.toFixed(1))
      },
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [structure, picks, node.charge, node.multiplicity])

  const pick = (next: number[]) => {
    setPicks(next)
    setMove(null)
    setTyped(next.map((i) => i + 1).join(' '))
  }

  const toggle = (index: number) => {
    if (picks.includes(index)) pick(picks.filter((i) => i !== index))
    else pick(picks.length >= 4 ? [index] : [...picks, index])
  }

  const run = async (tool: BuilderTool, options: BuilderOptions = {}) => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.builderEdit(structure, picks, tool, options)
      setPast([...past, structure])
      setFuture([])
      setStructure({ rows: result.rows, added: result.added, removed: result.removed })
      pick(result.picks)
      setMessage(result.message)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const undo = () => {
    const previous = past[past.length - 1]
    if (!previous) return
    setPast(past.slice(0, -1))
    setFuture([structure, ...future])
    setStructure(previous)
    pick(picks.filter((i) => i < previous.rows.length))
    setMessage('Undone')
    setError(null)
  }

  const redo = () => {
    const next = future[0]
    if (!next) return
    setFuture(future.slice(1))
    setPast([...past, structure])
    setStructure(next)
    pick(picks.filter((i) => i < next.rows.length))
    setMessage('Redone')
    setError(null)
  }

  const save = async (newNode: boolean) => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.setGeometry(node.id, xyz, newNode)
      onSaved(result.node, result.derived)
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  const close = () => (changed ? setClosing(true) : onClose())

  const addMolecule = () => {
    if (molecule.startsWith('node:')) {
      const other = nodes.find((n) => n.id === molecule.slice(5))
      if (other) void run('molecule', { atoms: rowsOf(other.xyz), what: `“${other.label || 'Untitled node'}”` })
    } else if (molecule) {
      void run('molecule', { name: molecule })
    }
  }

  const marks: AtomMarks = {
    labels: [],
    lines: [
      ...structure.added.map(([from, to]) => ({ from, to, colour: ADDED_BOND })),
      ...structure.removed.map(([from, to]) => ({ from, to, colour: REMOVED_BOND })),
    ],
  }

  const name = (i: number) => `${structure.rows[i]?.[0] ?? '?'}${i + 1}`
  const kind = described?.kind ?? null
  const sides = described?.sides ?? []
  const chosen = move ?? described?.default_side ?? null
  const numeric = Number(value)

  return (
    <Modal
      title={`Edit structure: “${node.label || 'Untitled node'}”`}
      onClose={close}
      wide
      actions={
        <>
          <button onClick={close}>Close</button>
          {!hasCalculations && (
            <button disabled={!changed || !count || busy} onClick={() => void save(true)} title="Keep this node as it is and save the structure as a new node derived from it">
              Save as a new node
            </button>
          )}
          <button className="primary" disabled={!changed || !count || busy} onClick={() => void save(hasCalculations)}>
            {hasCalculations ? 'Save as derived node' : 'Save'}
          </button>
        </>
      }
    >
      <div className="structure-editor">
        <div className="structure-editor-view">
          <HydrogenDisplay.Provider value="all">
            <Viewer3D
              models={xyz ? [{ xyz }] : []}
              rotation={node.view_rotation}
              picking={{ atoms: picks, onToggle: toggle }}
              marks={marks}
              keepView
            />
          </HydrogenDisplay.Provider>
          <p className="muted small" aria-label="Structure summary">
            {described ? `${described.formula || 'No atoms'} · ${described.atom_count} atoms` : ' '}
            {hasCalculations && ' · this node has calculations, so saving makes a derived node'}
          </p>
        </div>
        <div className="structure-editor-tools">
          <div className="row">
            <button className="small" disabled={!past.length || busy} onClick={undo}>
              Undo
            </button>
            <button className="small" disabled={!future.length || busy} onClick={redo}>
              Redo
            </button>
          </div>
          <label className="field">
            <span>Picked atoms</span>
            <span className="row">
              <input
                aria-label="Picked atoms"
                placeholder="click atoms, or type e.g. 1 2 3"
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') return
                  const next = typedPicks(typed, count)
                  if (next) pick(next)
                  else setError(`Give up to 4 different atom numbers between 1 and ${count}`)
                }}
              />
              <button
                className="small"
                onClick={() => {
                  const next = typedPicks(typed, count)
                  if (next) pick(next)
                  else setError(`Give up to 4 different atom numbers between 1 and ${count}`)
                }}
              >
                Pick
              </button>
              {picks.length > 0 && (
                <button className="small" onClick={() => pick([])}>
                  Clear
                </button>
              )}
            </span>
          </label>
          {picks.length > 0 && (
            <p className="mono small" aria-label="Picked">
              {picks.map(name).join('–')}
              {kind && described?.value !== null && described?.value !== undefined && (
                <>
                  : {described.value.toFixed(kind === 'distance' ? 3 : 1)} {UNITS[kind]}
                </>
              )}
            </p>
          )}

          {kind && (
            <section className="structure-editor-section" aria-label="Measure and set">
              <label className="field">
                <span>Set the {kind} to</span>
                <span className="row">
                  <input aria-label={`New ${kind}`} className="number" value={value} onChange={(event) => setValue(event.target.value)} />
                  <span>{UNITS[kind]}</span>
                  <button
                    className="small primary"
                    disabled={busy || value.trim() === '' || !Number.isFinite(numeric)}
                    onClick={() => void run(kind, { value: numeric, move: chosen })}
                  >
                    Set
                  </button>
                </span>
              </label>
              {sides.length === 2 && (
                <fieldset className="sides" aria-label="Which side moves">
                  <legend className="small">Moves</legend>
                  {sides.map((side) => (
                    <label key={side.atom} className="small">
                      <input type="radio" name="side" checked={chosen === side.atom} onChange={() => setMove(side.atom)} />
                      {side.whole
                        ? `${name(side.atom)}'s side (${side.count} atom${side.count === 1 ? '' : 's'})`
                        : `${name(side.atom)} and the atoms bonded only to it (${side.count})`}
                    </label>
                  ))}
                  {!sides[0].whole && <p className="muted small">The two sides are joined (a ring or a chelate), so only the end atom moves.</p>}
                </fieldset>
              )}
              {kind === 'distance' && (
                <div className="row">
                  {sides[0]?.whole && described?.bonded && (
                    <>
                      <span className="small">Turn about the bond by</span>
                      <input aria-label="Turn by" className="number" value={turn} onChange={(event) => setTurn(event.target.value)} />
                      <span className="small">°</span>
                      <button
                        className="small"
                        disabled={busy || !Number.isFinite(Number(turn))}
                        onClick={() => void run('rotate', { value: Number(turn), move: chosen })}
                      >
                        Turn
                      </button>
                    </>
                  )}
                  <button className="small" disabled={busy} onClick={() => void run('bond', { present: !described?.bonded })}>
                    {described?.bonded ? 'Remove bond' : 'Add bond'}
                  </button>
                </div>
              )}
            </section>
          )}

          {picks.length === 1 && (
            <section className="structure-editor-section" aria-label="Atom">
              <div className="row">
                <span className="small">Add</span>
                <input aria-label="Element to add" className="element" value={added} onChange={(event) => setAdded(event.target.value)} />
                <button className="small" disabled={busy || !added.trim()} onClick={() => void run('add_atom', { element: added })}>
                  Add atom
                </button>
              </div>
              <div className="row">
                <span className="small">Replace with</span>
                <select aria-label="Fragment" value={fragment} onChange={(event) => setFragment(event.target.value)}>
                  {(parts?.fragments ?? []).map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
                <button className="small" disabled={busy} onClick={() => void run('fragment', { name: fragment })}>
                  Replace
                </button>
              </div>
            </section>
          )}

          {picks.length > 0 && (
            <section className="structure-editor-section" aria-label="Change or delete">
              <div className="row">
                <span className="small">Element</span>
                <input aria-label="New element" className="element" value={element} onChange={(event) => setElement(event.target.value)} />
                <button className="small" disabled={busy || !element.trim()} onClick={() => void run('element', { element })}>
                  Change
                </button>
              </div>
              <div className="row">
                <button className="small" disabled={busy} onClick={() => void run('delete')}>
                  Delete
                </button>
                <button className="small" disabled={busy} onClick={() => void run('delete', { with_hydrogens: true })}>
                  Delete with its hydrogens
                </button>
              </div>
            </section>
          )}

          {picks.length <= 1 && (
            <section className="structure-editor-section" aria-label="Add a molecule">
              <div className="row">
                <span className="small">{picks.length ? `Beside ${name(picks[0])}` : 'Add'}</span>
                <select aria-label="Molecule" value={molecule} onChange={(event) => setMolecule(event.target.value)}>
                  <option value="">a molecule…</option>
                  <optgroup label="Built in">
                    {(parts?.molecules ?? []).map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </optgroup>
                  {others.length > 0 && (
                    <optgroup label="Nodes">
                      {others.map((n) => (
                        <option key={n.id} value={`node:${n.id}`}>
                          {n.label || 'Untitled node'}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
                <button className="small" disabled={busy || !molecule} onClick={addMolecule}>
                  Add
                </button>
              </div>
            </section>
          )}

          {(structure.added.length > 0 || structure.removed.length > 0) && (
            <section className="structure-editor-section" aria-label="Bonds changed">
              <p className="small">
                Bonds changed here decide which atoms move together; they are not saved with the coordinates.
              </p>
              <ul className="small">
                {structure.added.map(([a, b]) => (
                  <li key={`a${a}-${b}`} style={{ color: ADDED_BOND }}>
                    Added {name(a)}–{name(b)}
                  </li>
                ))}
                {structure.removed.map(([a, b]) => (
                  <li key={`r${a}-${b}`} style={{ color: REMOVED_BOND }}>
                    Removed {name(a)}–{name(b)}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {message && (
            <p className="small" role="status" aria-label="Last change">
              {message}
            </p>
          )}
          {error && (
            <p className="small warning" role="alert">
              {error}
            </p>
          )}
          {described?.electrons_note && (
            <p className="small warning" aria-label="Electrons">
              {described.electrons_note}
            </p>
          )}
        </div>
      </div>
      {closing && (
        <Modal
          title="Close without saving?"
          onClose={() => setClosing(false)}
          actions={
            <>
              <button onClick={() => setClosing(false)}>Keep editing</button>
              <button className="primary" onClick={onClose}>
                Discard changes
              </button>
            </>
          }
        >
          <p>The changes made in this window are not saved.</p>
        </Modal>
      )}
    </Modal>
  )
}
