import { useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from 'react'
import { defaultRotation, hiddenAtoms, measure, parseXyz, type HydrogenMode, type Rotation } from '../chem'
import { HydrogenDisplay } from '../display'
import { useTheme } from '../theme'
import { colourToken } from '../util'

type Viewer = import('3dmol').GLViewer

export type ViewerModel = { xyz: string; colour?: string; hidden?: boolean }

/** D80: clicking atoms of the first model adds them to (or takes them out of) a list, in
 * order, instead of measuring. */
export type AtomPicking = { atoms: number[]; onToggle: (index: number) => void }

const PICK_COLOURS = ['#f59f00', '#1c7ed6', '#e03131', '#2f9e44']

/** D113: numbers and highlights on atoms of the first model (0-based indices), and dashed
 * lines between atoms (bonds that form or break). */
export type AtomMarks = {
  labels: { index: number; text: string; colour?: string; highlight?: boolean }[]
  lines?: { from: number; to: number; colour: string }[]
}

function describe(result: ReturnType<typeof measure>): string | null {
  if (!result) return null
  if (result.kind === 'distance') return `Distance ${result.value.toFixed(3)} Å`
  if (result.kind === 'angle') return `Angle ${result.value.toFixed(1)}°`
  return `Dihedral ${result.value.toFixed(1)}°`
}

/** 3Dmol's viewer adds listeners to the window and the page's body that it never removes, so
 * every viewer made stayed in memory with its models. This makes one and keeps them, so
 * `release` can take them off again when the viewer goes. */
function createViewer($3Dmol: typeof import('3dmol'), element: HTMLElement, options: object) {
  const added: [EventTarget, string, EventListenerOrEventListenerObject][] = []
  const targets: EventTarget[] = [window, document.body]
  const originals = targets.map((target) => target.addEventListener)
  const own = targets.map((target) => Object.prototype.hasOwnProperty.call(target, 'addEventListener'))
  targets.forEach((target, i) => {
    target.addEventListener = function (
      this: EventTarget,
      type: string,
      listener: EventListenerOrEventListenerObject | null,
      ...rest: [(boolean | AddEventListenerOptions)?]
    ) {
      if (listener) added.push([target, type, listener])
      return originals[i].call(this, type, listener, ...rest)
    } as EventTarget['addEventListener']
  })
  try {
    const viewer = $3Dmol.createViewer(element, options)
    const release = () => added.forEach(([target, type, listener]) => target.removeEventListener(type, listener))
    return { viewer, release }
  } finally {
    targets.forEach((target, i) => {
      // Back to the inherited method, or to the target's own if it had one.
      if (own[i]) target.addEventListener = originals[i]
      else delete (target as { addEventListener?: unknown }).addEventListener
    })
  }
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
 * can be saved for the card, or cleared back to the default.
 *
 * For overlays (D80), a model can be hidden without losing the view, `picking` collects
 * alignment atoms, and `imageRef` is given a function returning the view as a PNG data URL.
 *
 * `corner` is drawn in the view's upper right corner (the pop-out button, D82).
 *
 * With `trajectory` (D101), the first model is a movie of the structures in `frames` (xyz
 * texts with the same atoms), showing the one at `index`; measuring follows that structure.
 * `below` is drawn under the view, inside the pop-out with it (the movie's controls).
 *
 * `marks` (D113) labels atoms and draws dashed lines between them, as the atom match does.
 *
 * With `keepView` (D139, the structure editor), a changed structure keeps the user's view and
 * turning instead of being zoomed to and turned to its default orientation. */
export function Viewer3D({
  models,
  vibration = null,
  rotation = null,
  onSaveRotation,
  picking,
  imageRef,
  corner,
  trajectory = null,
  below,
  marks,
  keepView = false,
}: {
  models: ViewerModel[]
  vibration?: { xyz: string; amplitude?: number } | null
  rotation?: Rotation | null
  onSaveRotation?: (rotation: Rotation | null) => void
  picking?: AtomPicking
  imageRef?: MutableRefObject<(() => string) | null>
  corner?: ReactNode
  trajectory?: { frames: string[]; index: number } | null
  below?: ReactNode
  marks?: AtomMarks
  keepView?: boolean
}) {
  const hydrogens = useContext(HydrogenDisplay)
  const host = useRef<HTMLDivElement>(null)
  const viewer = useRef<Viewer | null>(null)
  const releaseListeners = useRef<(() => void) | null>(null)
  const [ready, setReady] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [picks, setPicks] = useState<number[]>([])
  const [typed, setTyped] = useState('')

  const structures = models.map((m) => m.xyz).join('\n')
  const key = models.map((m) => `${m.colour ?? ''}:${m.hidden ? 'hidden' : ''}:${m.xyz}`).join('\n')
  const built = useRef<string | null>(null)
  const click = useRef<((atom: { serial?: number; index?: number }) => void) | null>(null)
  // The click handler is made once per build; it reads the current picking through this.
  const pickingRef = useRef(picking)
  pickingRef.current = picking
  const movie = vibration ? null : trajectory
  const frames = useMemo(() => movie?.frames.join('') ?? '', [movie?.frames])
  const shownXyz = movie ? movie.frames[movie.index] : models[0]?.xyz
  const atoms = useMemo(() => (shownXyz ? parseXyz(shownXyz) : []), [shownXyz])
  const empty = models.length === 0
  // The orientation to show: applied when the structure or the saved orientation changes,
  // not when a vibration starts or stops, so the user's own turning is kept then.
  const orientation = rotation ? rotation.join(',') : keepView ? 'default' : `default:${models[0]?.xyz ?? ''}`
  const applied = useRef<string | null>(null)

  // Models: rebuilt only when the structures or the vibration change.
  useEffect(() => {
    let cancelled = false
    // Picked atoms are kept while a movie of the same structure starts, stops or changes (D101).
    if (built.current !== structures) setPicks([])
    if (empty) return
    import('3dmol')
      .then(($3Dmol) => {
        if (cancelled || !host.current) return
        if (!viewer.current) {
          const made = createViewer($3Dmol, host.current, { backgroundColor: colourToken('--viewer-bg') })
          viewer.current = made.viewer
          releaseListeners.current = made.release
        }
        const v = viewer.current
        // Showing or hiding a structure, or starting or stopping a movie of it (D101), keeps
        // the user's view; new structures are zoomed to.
        const kept =
          !vibration && (built.current === structures || (keepView && built.current !== null)) ? v.getView() : null
        built.current = vibration ? null : structures
        click.current = (atom: { serial?: number; index?: number }) => {
          const index = atom.serial ?? atom.index
          if (index === undefined) return
          if (pickingRef.current) {
            pickingRef.current.onToggle(index)
            return
          }
          setPicks((current) => (current.length >= 4 ? [index] : current.includes(index) ? current : [...current, index]))
        }
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
            const model = index === 0 && frames ? v.addModelsAsFrames(frames, 'xyz') : v.addModel(m.xyz, 'xyz')
            if (m.hidden) {
              model.setStyle({}, {})
              return
            }
            const colour = m.colour ? { color: m.colour } : {}
            model.setStyle({}, { stick: { radius: index ? 0.1 : 0.14, ...colour }, sphere: { scale: index ? 0.18 : 0.25, ...colour } })
            hideHydrogens(model, m.xyz, hydrogens)
          })
          v.setClickable({ model: 0 }, true, click.current)
        }
        if (kept) v.setView(kept)
        else v.zoomTo()
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
  }, [key, vibration, empty, hydrogens, frames])

  // D101: the movie's structure. Clicks are set per structure, as 3Dmol keeps them per atom.
  const frameIndex = movie?.index ?? 0
  const frameCount = movie?.frames.length ?? 0
  useEffect(() => {
    const v = viewer.current
    const model = v?.getModel(0)
    // Only the movie's own model: until it is built, the view may still hold another one.
    if (!v || !model || !ready || !frameCount || model.getNumFrames() !== frameCount) return
    void model.setFrame(frameIndex).then(() => {
      if (click.current) model.setClickable({}, true, click.current)
      v.render()
    })
  }, [ready, frames, frameIndex, frameCount])

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

  // D185: the view's background follows the theme.
  const theme = useTheme()
  useEffect(() => {
    viewer.current?.setBackgroundColor(colourToken('--viewer-bg'), 1)
    viewer.current?.render()
  }, [theme])

  useEffect(() => {
    if (!imageRef) return
    // D185: the picture is always on white, whatever the theme.
    imageRef.current = () => {
      const v = viewer.current
      if (!v) return ''
      v.setBackgroundColor(colourToken('--paper'), 1)
      v.render()
      const image = v.pngURI()
      v.setBackgroundColor(colourToken('--viewer-bg'), 1)
      v.render()
      return image
    }
    return () => {
      imageRef.current = null
    }
  }, [imageRef])

  // Picked atoms: highlighted, joined by dashed lines and numbered. Alignment atoms being
  // picked (D80) are numbered in their order, without lines. Callers build `marks` and the
  // picked list anew on every render (each keystroke in the structure editor), so they are
  // compared by content: otherwise every label was drawn again on each.
  const marksKey = marks ? JSON.stringify(marks) : ''
  const shownMarks = useMemo(() => (marksKey ? (JSON.parse(marksKey) as AtomMarks) : undefined), [marksKey])
  const aligningKey = picking ? picking.atoms.join(',') : null
  const aligning = useMemo(
    () => (aligningKey === null ? undefined : aligningKey ? aligningKey.split(',').map(Number) : []),
    [aligningKey],
  )
  const drawn = useRef(false)
  useEffect(() => {
    const v = viewer.current
    if (!v || !ready) return
    const marks = shownMarks
    const nothing = !marks?.lines?.length && !marks?.labels.length && !aligning?.length && !picks.length
    // A movie's frames change `atoms`; with nothing drawn on them, there is nothing to redo.
    if (nothing && !drawn.current) return
    drawn.current = !nothing
    v.removeAllShapes()
    v.removeAllLabels()
    const at = (index: number) => {
      const atom = atoms[index]
      return atom ? { x: atom.x, y: atom.y, z: atom.z } : null
    }
    marks?.lines?.forEach(({ from, to, colour }) => {
      const start = at(from)
      const end = at(to)
      if (start && end) v.addCylinder({ start, end, radius: 0.07, color: colour, dashed: true })
    })
    marks?.labels.forEach(({ index, text, colour, highlight }) => {
      const center = at(index)
      if (!center) return
      // A halo a little larger than the atom's ball: a hydrogen's is small, a metal's or a
      // halogen's as large as the old fixed halo.
      const radius = atoms[index]?.element === 'H' ? 0.45 : 0.62
      if (highlight) v.addSphere({ center, radius, color: colour ?? PICK_COLOURS[0], alpha: 0.6 })
      if (!text) return // a highlight only
      v.addLabel(text, {
        position: center,
        fontSize: 10,
        fontColor: 'black',
        backgroundColor: colour ?? 'white',
        backgroundOpacity: 0.75,
        inFront: true,
      })
    })
    if (aligning) {
      aligning.forEach((index, i) => {
        const atom = atoms[index]
        if (!atom) return
        const center = { x: atom.x, y: atom.y, z: atom.z }
        v.addSphere({ center, radius: 0.45, color: PICK_COLOURS[0], alpha: 0.55 })
        v.addLabel(`${i + 1}: ${atom.element}${index + 1}`, {
          position: center,
          fontSize: 11,
          backgroundColor: PICK_COLOURS[0],
          backgroundOpacity: 0.8,
        })
      })
      v.render()
      return
    }
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
        v.addCylinder({ start: center, end: { x: next.x, y: next.y, z: next.z }, radius: 0.04, color: colourToken('--viewer-line'), dashed: true })
      }
    })
    v.render()
  }, [picks, aligning, atoms, ready, shownMarks])

  useEffect(() => {
    const element = host.current
    if (!element) return
    const observer = new ResizeObserver(() => viewer.current?.resize())
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // A viewer that goes (a node inspector per selected node, an Inspect window per file) gives
  // back its models and stops watching its element; 3Dmol has no call for that. Its WebGL
  // context is shared by every viewer of the page, so it is not lost here.
  useEffect(
    () => () => {
      const v = viewer.current
      viewer.current = null
      if (!v) return
      try {
        v.stopAnimate()
        v.clear()
      } catch {
        // Already torn down
      }
      // Private in 3Dmol's types: the observers it starts on its element.
      const watchers = v as unknown as { divwatcher?: ResizeObserver; intwatcher?: IntersectionObserver }
      watchers.divwatcher?.disconnect()
      watchers.intwatcher?.disconnect()
      releaseListeners.current?.()
      releaseListeners.current = null
    },
    [],
  )

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
        {corner && <div className="viewer-corner">{corner}</div>}
      </div>
      {below}
      {!empty && !vibration && !picking && (
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
