import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import {
  api,
  ApiError,
  geometryToXyz,
  type BuilderDescription,
  type BuilderHold,
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

// D142: Ketcher is large, so it is loaded only when Draw… is first opened.
const KetcherDrawer = lazy(() => import('./KetcherDrawer'))

const ADDED_BOND = '#2f9e44'
const REMOVED_BOND = '#e03131'
const FROZEN = '#1971c2'

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
 * change can be undone until the window is closed; nothing is stored before saving. D140: atoms
 * frozen and distances, angles and dihedrals held for Minimize, a quick UFF clean-up of what
 * changed (or everything). D141: a lone atom to start from, hydrogens up to a number of bonds,
 * and molecules and fragments from SMILES. */
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
  const start: BuilderStructure = useMemo(
    () => ({ rows: rowsOf(node.xyz), added: [], removed: [], changed: [], holds: [] }),
    [node.xyz],
  )
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
  const [fragmentSmiles, setFragmentSmiles] = useState('')
  const [smiles, setSmiles] = useState('')
  const [bonds, setBonds] = useState('')
  const [lone, setLone] = useState('C')
  const [molecule, setMolecule] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [closing, setClosing] = useState(false)
  const [everything, setEverything] = useState(false)
  const [relaxMetals, setRelaxMetals] = useState(false)
  // D142: Draw… (a new molecule, the drawing kept as `drawn`); D143: Draw from this structure.
  const [drawing, setDrawing] = useState<null | 'new' | { molfile: string }>(null)
  const [drawn, setDrawn] = useState<string | null>(null)
  // D166: bonds ticked to be left out when flipping (atom pairs from 0); cleared on a new pick.
  const [ignore, setIgnore] = useState<[number, number][]>([])

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
    api.builderDescribe(structure, picks, node.charge, node.multiplicity, ignore).then(
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
  }, [structure, picks, node.charge, node.multiplicity, ignore])

  const pick = (next: number[]) => {
    setPicks(next)
    setMove(null)
    setIgnore([])
    setTyped(next.map((i) => i + 1).join(' '))
  }

  const toggle = (index: number) => {
    if (picks.includes(index)) pick(picks.filter((i) => i !== index))
    else pick(picks.length >= 4 ? [index] : [...picks, index])
  }

  const run = async (tool: BuilderTool, options: BuilderOptions = {}, on: number[] = picks): Promise<boolean> => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.builderEdit(structure, on, tool, options)
      setPast([...past, structure])
      setFuture([])
      const { rows, added, removed, changed, holds, minimized_from, minimized_metals } = result
      setStructure({ rows, added, removed, changed, holds, minimized_from, minimized_metals })
      // Minimize and the list of holds leave the picked atoms as they were.
      if (tool !== 'minimize' && on === picks) pick(result.picks)
      setMessage(result.message)
      return true
    } catch (err) {
      setError(errorText(err))
      return false
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

  const drawFromStructure = async () => {
    setBusy(true)
    setError(null)
    try {
      setDrawing(await api.builderDrawing(structure))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const addMolecule = () => {
    if (molecule.startsWith('node:')) {
      const other = nodes.find((n) => n.id === molecule.slice(5))
      if (other) void run('molecule', { atoms: rowsOf(other.xyz), what: `“${other.label || 'Untitled node'}”` })
    } else if (molecule) {
      void run('molecule', { name: molecule })
    }
  }

  const frozen = structure.holds.filter((h) => h.atoms.length === 1).map((h) => h.atoms[0])
  const marks: AtomMarks = {
    labels: frozen.map((index) => ({ index, text: 'frozen', colour: FROZEN })),
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
  const flip = kind === 'angle' ? (described?.flip ?? null) : null
  const sameBond = (a: [number, number], b: [number, number]) => a[0] === b[0] && a[1] === b[1]
  // D166: the tie's bonds and those already ticked, which stay listed to be unticked.
  const tieBonds = flip ? [...ignore, ...flip.tie.filter((bond) => !ignore.some((i) => sameBond(i, bond)))] : []

  return (
    <Modal
      title={`Edit structure: “${node.label || 'Untitled node'}”`}
      onClose={drawing ? () => undefined : close}
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
            <button
              className="small"
              disabled={!count || busy}
              onClick={() => void drawFromStructure()}
              title="Change the structure in 2D; atoms you leave as they are stay where they are in 3D"
            >
              Draw from this structure…
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
                  <button
                    className="small"
                    disabled={busy || value.trim() === '' || !Number.isFinite(numeric)}
                    title={`Keep this ${kind} at the value typed when minimizing`}
                    onClick={() => void run('hold', { value: numeric })}
                  >
                    Hold
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
              {flip && (
                <div className="row" aria-label="Flip">
                  <button
                    className="small"
                    disabled={busy || flip.error !== null || flip.tie.length > 0}
                    title={
                      flip.error ??
                      (flip.tie.length
                        ? 'Tied to the rest past the third atom: tick a bond to ignore'
                        : `Swap ${name(picks[0])} and ${name(picks[1])} by a 180° turn about the line from their middle towards ${name(picks[2])} (${flip.count} atoms turn)`)
                    }
                    onClick={() => void run('flip', { ignore })}
                  >
                    Flip {name(picks[0])} ↔ {name(picks[1])}
                  </button>
                  {tieBonds.length > 0 && (
                    <span className="flip-tie small" aria-label="Bonds to ignore">
                      Tied; ignore
                      {tieBonds.map((bond) => (
                        <label key={bond.join('-')}>
                          <input
                            type="checkbox"
                            checked={ignore.some((i) => sameBond(i, bond))}
                            onChange={(event) =>
                              setIgnore(event.target.checked ? [...ignore, bond] : ignore.filter((i) => !sameBond(i, bond)))
                            }
                          />
                          {name(bond[0])}–{name(bond[1])}
                        </label>
                      ))}
                    </span>
                  )}
                </div>
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
                  <option value="*">SMILES…</option>
                </select>
                {fragment === '*' && (
                  <input
                    aria-label="Fragment SMILES"
                    className="mono"
                    placeholder="*c1ccccc1"
                    title="A SMILES with one * where it attaches"
                    value={fragmentSmiles}
                    onChange={(event) => setFragmentSmiles(event.target.value)}
                  />
                )}
                <button
                  className="small"
                  disabled={busy || (fragment === '*' && !fragmentSmiles.trim())}
                  onClick={() => void run('fragment', fragment === '*' ? { smiles: fragmentSmiles } : { name: fragment })}
                >
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
              <div className="row">
                <span className="small">Add H until</span>
                <input
                  aria-label="Bonds wanted"
                  className="element"
                  placeholder="usual"
                  title="Leave empty for the element's usual number (C 4, N 3, O 2)"
                  value={bonds}
                  onChange={(event) => setBonds(event.target.value)}
                />
                <span className="small">bonds</span>
                <button
                  className="small"
                  disabled={busy || (bonds.trim() !== '' && !Number.isInteger(Number(bonds)))}
                  onClick={() => void run('hydrogens', bonds.trim() ? { bonds: Number(bonds) } : {})}
                >
                  Add hydrogens
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
              <div className="row">
                <span className="small">From SMILES</span>
                <input aria-label="SMILES" className="mono" placeholder="CC(=O)O" value={smiles} onChange={(event) => setSmiles(event.target.value)} />
                <button className="small" disabled={busy || !smiles.trim()} onClick={() => void run('smiles', { smiles })}>
                  Add SMILES
                </button>
              </div>
              <div className="row">
                <span className="small">Drawn in 2D</span>
                <button
                  className="small"
                  disabled={busy}
                  onClick={() => {
                    setError(null)
                    setDrawing('new')
                  }}
                  title="Draw a molecule, or a fragment with an attachment point, and convert it to 3D"
                >
                  Draw…
                </button>
              </div>
              {picks.length === 0 && (
                <div className="row">
                  <span className="small">A lone atom</span>
                  <input aria-label="Lone atom" className="element" value={lone} onChange={(event) => setLone(event.target.value)} />
                  <button className="small" disabled={busy || !lone.trim()} onClick={() => void run('add_atom', { element: lone })}>
                    Add atom
                  </button>
                </div>
              )}
            </section>
          )}

          <section className="structure-editor-section" aria-label="Minimize">
            {picks.length > 0 && (
              <div className="row">
                <button className="small" disabled={busy} onClick={() => void run('freeze')} title="Keep the picked atoms where they are when minimizing">
                  Freeze
                </button>
                {picks.some((i) => frozen.includes(i)) && (
                  <button className="small" disabled={busy} onClick={() => void run('unfreeze')}>
                    Unfreeze
                  </button>
                )}
              </div>
            )}
            {structure.holds.length > 0 && (
              <ul className="small holds" aria-label="Held">
                {structure.holds.map((hold, index) => (
                  <HoldRow
                    key={`${hold.atoms.join('-')}:${hold.value ?? ''}`}
                    hold={hold}
                    names={hold.atoms.map(name)}
                    busy={busy}
                    onValue={(v) => void run('hold', { value: v }, hold.atoms)}
                    onRelease={() => void run('release', { index })}
                  />
                ))}
              </ul>
            )}
            <fieldset className="sides" aria-label="What Minimize moves">
              <label className="small">
                <input type="radio" name="minimize" checked={!everything && structure.changed.length > 0} disabled={!structure.changed.length} onChange={() => setEverything(false)} />
                {structure.changed.length
                  ? `The ${structure.changed.length} changed atom${structure.changed.length === 1 ? '' : 's'} and two bonds round them`
                  : 'What changed (nothing yet)'}
              </label>
              <label className="small">
                <input type="radio" name="minimize" checked={everything || !structure.changed.length} onChange={() => setEverything(true)} />
                Everything
              </label>
            </fieldset>
            <fieldset className="sides" aria-label="Metals">
              <label className="small" title="Each metal keeps its bond lengths and angles (D140)">
                <input type="radio" name="metals" checked={!relaxMetals} onChange={() => setRelaxMetals(false)} />
                Metals keep their shape
              </label>
              <label className="small" title="Metal bonds go to UFF's lengths and the angles to the nearest ideal shape (D146)">
                <input type="radio" name="metals" checked={relaxMetals} onChange={() => setRelaxMetals(true)} />
                Metals relax
              </label>
            </fieldset>
            <div className="row">
              <button className="small" disabled={busy || count < 2} onClick={() => void run('minimize', { everything: everything || !structure.changed.length, metals: relaxMetals ? 'relax' : 'keep' })}>
                Minimize
              </button>
              <span className="muted small">A quick UFF clean-up; frozen and held stay.</span>
            </div>
          </section>

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
      {drawing && (
        <Suspense
          fallback={
            <div className="ketcher-drawer" role="status">
              Loading the drawer…
            </div>
          }
        >
          <KetcherDrawer
            start={drawing === 'new' ? drawn : drawing.molfile}
            help={
              drawing !== 'new'
                ? 'Change the structure in 2D. Convert to 3D puts it back: atoms left as they were stay where they are in 3D, and Minimize cleans up round the changes.'
                : picks.length === 1
                  ? `Convert to 3D adds the drawing beside ${name(picks[0])}; a drawing with an attachment point (or an R# atom) replaces ${name(picks[0])}.`
                  : 'Convert to 3D adds the drawing to the structure. To replace an atom with a drawn fragment, pick it first and draw an attachment point.'
            }
            busy={busy}
            error={error}
            onConvert={(d) => {
              if (drawing === 'new') setDrawn(d.ket)
              void run(drawing === 'new' ? 'drawing' : 'redraw', { molfile: d.molfile }).then(
                (done) => done && setDrawing(null),
              )
            }}
            onClose={(ket) => {
              if (drawing === 'new' && ket !== null) setDrawn(ket)
              setDrawing(null)
            }}
          />
        </Suspense>
      )}
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

const HOLD_NAMES = { 2: 'Distance', 3: 'Angle', 4: 'Dihedral' } as Record<number, string>

/** D140: one entry of the list of holds; a held value can be changed in place. */
function HoldRow({
  hold,
  names,
  busy,
  onValue,
  onRelease,
}: {
  hold: BuilderHold
  names: string[]
  busy: boolean
  onValue: (value: number) => void
  onRelease: () => void
}) {
  const distance = hold.atoms.length === 2
  const shown = hold.value === null ? '' : hold.value.toFixed(distance ? 3 : 1)
  const [text, setText] = useState(shown)
  const commit = () => {
    const v = Number(text)
    if (text.trim() !== '' && Number.isFinite(v) && text !== shown) onValue(v)
  }
  return (
    <li className="row">
      {hold.atoms.length === 1 ? (
        <span style={{ color: FROZEN }}>{names[0]} frozen</span>
      ) : (
        <>
          <span>
            {HOLD_NAMES[hold.atoms.length]} {names.join('–')} at
          </span>
          <input
            aria-label={`Held ${HOLD_NAMES[hold.atoms.length].toLowerCase()} ${names.join('–')}`}
            className="number"
            value={text}
            disabled={busy}
            onChange={(event) => setText(event.target.value)}
            onBlur={commit}
            onKeyDown={(event) => event.key === 'Enter' && commit()}
          />
          <span>{distance ? 'Å' : '°'}</span>
        </>
      )}
      <button className="small" disabled={busy} onClick={onRelease} aria-label={`Stop holding ${names.join('–')}`} title="Stop holding">
        ×
      </button>
    </li>
  )
}
