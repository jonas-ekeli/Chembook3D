import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { defaultRotation, hiddenAtoms, measure, parseXyz, type HydrogenMode, type Rotation } from '../chem'
import { HydrogenDisplay } from '../display'

type Viewer = import('3dmol').GLViewer

export type ViewerModel = { xyz: string; colour?: string }

const PICK_COLOURS = ['#f59f00', '#1c7ed6', '#e03131', '#2f9e44']

function describe(result: ReturnType<typeof measure>): string | null {
  if (!result) return null
  if (result.kind === 'distance') return `Distance ${result.value.toFixed(3)} Å`
  if (result.kind === 'angle') return `Angle ${result.value.toFixed(1)}°`
  return `Dihedral ${result.value.toFixed(1)}°`
}

function hideHydrogens(model: import('3dmol').GLModel, xyz: string, mode: HydrogenMode) {
  const hidden = hiddenAtoms(parseXyz(xyz), mode)
  if (hidden.size > 0) model.setStyle({ index: [...hidden] }, {})
}

/** View-only 3D structure (FR-3D-01…04, D20): rotate, zoom and pan with the mouse; click up to
 * four atoms of the first model to measure a distance, angle or dihedral (or type their
 * numbers); animate a vibration; show a second, aligned model in its own colour. There is
 * no geometry editing here (X3). 3Dmol.js is loaded on first use.
 *
 * The view opens turned by `rotation` (the node's saved orientation) or, without one, by the
 * default orientation its structure card uses. With `onSaveRotation`, the current orientation
 * can be saved for the card, or cleared back to the default. */
export function Viewer3D({
  models,
  vibration = null,
  rotation = null,
  onSaveRotation,
}: {
  models: ViewerModel[]
  vibration?: { xyz: string; amplitude?: number } | null
  rotation?: Rotation | null
  onSaveRotation?: (rotation: Rotation | null) => void
}) {
  const hydrogens = useContext(HydrogenDisplay)
  const host = useRef<HTMLDivElement>(null)
  const viewer = useRef<Viewer | null>(null)
  const [ready, setReady] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [picks, setPicks] = useState<number[]>([])
  const [typed, setTyped] = useState('')

  const key = models.map((m) => `${m.colour ?? ''}:${m.xyz}`).join('\n')
  const atoms = useMemo(() => (models[0] ? parseXyz(models[0].xyz) : []), [models])
  const empty = models.length === 0
  // The orientation to show: applied when the structure or the saved orientation changes,
  // not when a vibration starts or stops, so the user's own turning is kept then.
  const orientation = rotation ? rotation.join(',') : `default:${models[0]?.xyz ?? ''}`
  const applied = useRef<string | null>(null)

  // Models: rebuilt only when the structures or the vibration change.
  useEffect(() => {
    let cancelled = false
    setPicks([])
    if (empty) return
    import('3dmol')
      .then(($3Dmol) => {
        if (cancelled || !host.current) return
        if (!viewer.current) viewer.current = $3Dmol.createViewer(host.current, { backgroundColor: 'white' })
        const v = viewer.current
        v.stopAnimate()
        v.clear()
        if (vibration) {
          const model = v.addModel(vibration.xyz, 'xyz')
          model.setStyle({}, { stick: { radius: 0.14 }, sphere: { scale: 0.25 } })
          hideHydrogens(model, vibration.xyz, hydrogens)
          model.vibrate(12, vibration.amplitude ?? 1, true)
          v.animate({ loop: 'backAndForth', reps: 0, interval: 40 })
        } else {
          models.forEach((m, index) => {
            const model = v.addModel(m.xyz, 'xyz')
            const colour = m.colour ? { color: m.colour } : {}
            model.setStyle({}, { stick: { radius: index ? 0.1 : 0.14, ...colour }, sphere: { scale: index ? 0.18 : 0.25, ...colour } })
            hideHydrogens(model, m.xyz, hydrogens)
          })
          v.setClickable({ model: 0 }, true, (atom: { serial?: number; index?: number }) => {
            const index = atom.serial ?? atom.index
            if (index === undefined) return
            setPicks((current) => (current.length >= 4 ? [index] : current.includes(index) ? current : [...current, index]))
          })
        }
        v.zoomTo()
        v.render()
        setError(null)
        setReady((n) => n + 1)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(`3D view unavailable: ${String(err)}`)
      })
    return () => {
      cancelled = true
    }
    // `key` stands for `models`, which is a new array on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, vibration, empty, hydrogens])

  // Orientation: the saved one, or the default the structure card uses.
  useEffect(() => {
    const v = viewer.current
    if (!v || !ready || applied.current === orientation) return
    const q = rotation ?? defaultRotation(atoms)
    const view = v.getView()
    view.splice(4, 4, ...q)
    v.setView(view)
    applied.current = orientation
    // `orientation` stands for `rotation` and the first model's atoms.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, orientation])

  const saveRotation = () => {
    const v = viewer.current
    if (!v || !onSaveRotation) return
    const [x, y, z, w] = v.getView().slice(4, 8)
    const length = Math.hypot(x, y, z, w) || 1
    const q: Rotation = [x / length, y / length, z / length, w / length]
    // The view already shows it; don't turn it again when the saved value comes back.
    applied.current = q.join(',')
    onSaveRotation(q)
  }

  // Picked atoms: highlighted, joined by dashed lines and numbered.
  useEffect(() => {
    const v = viewer.current
    if (!v || !ready) return
    v.removeAllShapes()
    v.removeAllLabels()
    picks.forEach((index, i) => {
      const atom = atoms[index]
      if (!atom) return
      const center = { x: atom.x, y: atom.y, z: atom.z }
      v.addSphere({ center, radius: 0.45, color: PICK_COLOURS[i], alpha: 0.55 })
      v.addLabel(`${i + 1}: ${atom.element}${index + 1}`, {
        position: center,
        fontSize: 11,
        backgroundColor: PICK_COLOURS[i],
        backgroundOpacity: 0.8,
      })
      const next = atoms[picks[i + 1]]
      if (next) {
        v.addCylinder({ start: center, end: { x: next.x, y: next.y, z: next.z }, radius: 0.04, color: '#495057', dashed: true })
      }
    })
    v.render()
  }, [picks, atoms, ready])

  useEffect(() => {
    const element = host.current
    if (!element) return
    const observer = new ResizeObserver(() => viewer.current?.resize())
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const result = describe(measure(picks.map((i) => atoms[i]).filter(Boolean)))

  const applyTyped = () => {
    const numbers = typed
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((t) => Number.parseInt(t, 10) - 1)
    if (numbers.length >= 2 && numbers.length <= 4 && numbers.every((n) => n >= 0 && n < atoms.length)) {
      setPicks(numbers)
      setError(null)
    } else {
      setError(`Give 2 to 4 atom numbers between 1 and ${atoms.length}.`)
    }
  }

  return (
    <div className="viewer-block">
      <div className="viewer">
        <div ref={host} className="viewer-canvas" data-testid="viewer3d" hidden={empty} />
        {empty && <p className="muted viewer-empty">No coordinates yet.</p>}
      </div>
      {!empty && !vibration && (
        <div className="measure" aria-label="Measure">
          <span className="muted small">Click 2–4 atoms, or type their numbers:</span>
          <input
            aria-label="Atom numbers"
            placeholder="e.g. 1 2 3"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => event.key === 'Enter' && applyTyped()}
          />
          <button className="small" onClick={applyTyped}>
            Measure
          </button>
          {picks.length > 0 && (
            <button className="small" onClick={() => setPicks([])}>
              Clear
            </button>
          )}
          <span aria-label="Measurement" className="mono measurement">
            {picks.length > 0 && (
              <>
                {picks.map((i) => `${atoms[i]?.element ?? '?'}${i + 1}`).join('–')}
                {result && <>: {result}</>}
              </>
            )}
          </span>
        </div>
      )}
      {!empty && !vibration && onSaveRotation && (
        <div className="viewer-orientation" aria-label="Card orientation">
          <button
            className="small"
            onClick={saveRotation}
            title="Draw this node's structure card the way it is turned here"
          >
            Save orientation for card
          </button>
          {rotation && (
            <button
              className="small"
              onClick={() => onSaveRotation(null)}
              title="Forget the saved orientation; the card and this view use the default again"
            >
              Reset to default
            </button>
          )}
          <span className="muted small">{rotation ? 'The card uses the saved orientation.' : 'The card uses the default orientation.'}</span>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
