import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  ApiError,
  CALCULATION_TYPES,
  formatDelta,
  geometryToXyz,
  type Inspection,
  type InspectStep,
  type InspectTarget,
  type Settings,
} from '../api'
import { hartree, withDisplacements } from '../util'
import { Modal } from './Modal'
import { Convergence, EnergyChart } from './StepMovie'
import { Viewer3D } from './Viewer3D'

// D123c: the Inspect window reads an output or xyz file, on this computer or on a server,
// without importing it: its job steps, the structures of a step with their energies and
// convergence criteria, and its normal modes. Refresh reads it again for a job still running
// and keeps the structure shown; "Import this file" goes on to the import with the same copy.
// A scan shows only the structure each point converged on unless every structure is asked for,
// as the step movie does (D125, D101). Several files (`inspect *.log`, several chosen in "Import
// from here", a name filter in the file browser) are stepped through with Previous and Next, one
// read at a time; the window's choices (Every 30 s, converged points, speed) stay from file to
// file (D129). "Copy XYZ" copies the structure shown, the one the player has reached, as an
// xyz text with the file, step, structure and energy on its comment line (D130).

const SPEEDS = [1, 2, 5, 10, 20, 30]
const AUTO_REFRESH = 30_000 // ms (A70)
// A step opens on its last structure, the latest of a job still running, and stays on the last
// one as Refresh brings more until another is chosen.
const LAST = Number.MAX_SAFE_INTEGER

function errorText(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === 'string') return err.detail
  return err instanceof Error ? err.message : String(err)
}

function jobLabel(step: InspectStep): string {
  if (step.job_type === 'structures') return 'Structures'
  const label = CALCULATION_TYPES[step.job_type] ?? step.job_type
  return step.scan ? `${label} (${step.scan} scan)` : label
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} kB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

/** The step a file opens on: the last with a movie (an optimization or scan), else the last
 * with a structure. */
function firstShown(steps: InspectStep[]): number {
  const movie = [...steps].reverse().find((s) => s.frames.length > 1)
  const any = [...steps].reverse().find((s) => s.frames.length > 0)
  return (movie ?? any ?? steps[steps.length - 1])?.index ?? 1
}

function baseName(path: string): string {
  return path.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || path
}

/** The comment line of a copied structure: where it comes from and its energy (D130). */
function copiedComment(
  file: string,
  step: InspectStep,
  frame: InspectStep['frames'][number],
  n: number,
): string {
  const parts = [file, `step ${step.index}`, `structure ${n + 1} of ${step.frames.length}`]
  if (frame.point !== null && frame.point !== undefined) parts.push(`scan point ${frame.point} of ${step.points}`)
  if (frame.converged) parts.push('converged')
  if (frame.energy !== null && frame.energy !== undefined) parts.push(`E = ${frame.energy} Eh`)
  return parts.join(', ')
}

function frequency(value: number): string {
  return value < 0 ? `${Math.abs(value).toFixed(1)}i` : value.toFixed(1)
}

export function InspectWindow({
  targets,
  settings,
  canImport,
  onClose,
  onImport,
}: {
  /** The files to look at, in order; the first is shown first (D129). */
  targets: InspectTarget[]
  settings: Settings | null
  /** An investigation is open, so the file can be imported into it. */
  canImport: boolean
  onClose: () => void
  /** Go on to the import dialog with this inspection's copy. */
  onImport: (inspection: Inspection) => void
}) {
  const [file, setFile] = useState(0)
  const target = targets[Math.min(file, targets.length - 1)]
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [stepIndex, setStepIndex] = useState<number | null>(null)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(5)
  const [mode, setMode] = useState('')
  const [convergedOnly, setConvergedOnly] = useState(true)
  const [auto, setAuto] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  // The window's inspection, to close it on the server when the window goes.
  const opened = useRef<string | null>(null)
  const closing = useRef(false)

  useEffect(() => {
    let cancelled = false
    api.inspect(target).then(
      (found) => {
        opened.current = found.id
        if (cancelled) {
          void api.closeInspection(found.id)
          return
        }
        setInspection(found)
        setStepIndex(firstShown(found.steps))
        setIndex(LAST)
        setBusy(false)
      },
      (err: unknown) => {
        if (cancelled) return
        setError(errorText(err))
        setBusy(false)
      },
    )
    return () => {
      cancelled = true
      if (opened.current && !closing.current) void api.closeInspection(opened.current)
      opened.current = null
    }
  }, [target])

  const refresh = useCallback(() => {
    const id = opened.current
    if (!id) return
    setBusy(true)
    api.refreshInspection(id).then(
      (found) => {
        if (opened.current !== id) return // another file is shown now
        setInspection(found)
        setError(null)
        setBusy(false)
        // The same step if it is still there; the structure shown is kept by its number.
        setStepIndex((current) =>
          current !== null && found.steps.some((s) => s.index === current) ? current : firstShown(found.steps),
        )
      },
      (err: unknown) => {
        if (opened.current !== id) return
        setError(errorText(err))
        setBusy(false)
      },
    )
  }, [])

  useEffect(() => {
    if (!auto) return
    const timer = setInterval(refresh, AUTO_REFRESH)
    return () => clearInterval(timer)
  }, [auto, refresh])

  const step = useMemo(() => inspection?.steps.find((s) => s.index === stepIndex) ?? null, [inspection, stepIndex])
  const frames = useMemo(() => step?.frames ?? [], [step])
  // D125: a scan whose points have converged can be shown one structure per point; until the
  // first point of a running scan converges, every structure is shown.
  const filterable =
    step?.scan !== null && step?.scan !== undefined && frames.some((f) => f.converged) && frames.some((f) => !f.converged)
  const shown = useMemo(() => {
    const all = frames.map((frame, n) => ({ frame, n }))
    return filterable && convergedOnly ? all.filter(({ frame }) => frame.converged) : all
  }, [frames, filterable, convergedOnly])
  const count = shown.length
  const at = Math.min(index, Math.max(count - 1, 0))
  const xyzFrames = useMemo(
    () => shown.map(({ frame, n }) => geometryToXyz(frame.geometry, `structure ${n + 1}`)),
    [shown],
  )
  const modes = step?.modes ?? null
  const vibration = useMemo(() => {
    const chosen = mode === '' ? -1 : Number(mode)
    return modes && chosen >= 0 ? { xyz: withDisplacements(modes.xyz, modes.modes[chosen]) } : null
  }, [modes, mode])

  useEffect(() => {
    if (!playing || count < 2 || vibration) return
    const timer = setInterval(() => setIndex((i) => (Math.min(i, count - 1) + 1) % count), 1000 / speed)
    return () => clearInterval(timer)
  }, [playing, speed, count, vibration])

  const choose = (next: number) => {
    setStepIndex(next)
    setIndex(LAST)
    setPlaying(false)
    setMode('')
  }
  const move = (by: number) => {
    setPlaying(false)
    setIndex((at + by + count) % count)
  }
  /** Shows the scan's converged points only, or every structure, keeping the structure shown
   * (its point's converged structure), or the last while the last is shown. */
  const showConverged = (only: boolean) => {
    setConvergedOnly(only)
    if (index >= count - 1) return setIndex(LAST)
    const keep = shown[at]?.n ?? 0
    const next = frames.map((frame, n) => ({ frame, n })).filter(({ frame }) => !only || frame.converged)
    const found = next.findIndex(({ n }) => n >= keep)
    setIndex(found < 0 ? LAST : found)
  }
  /** D129: show another of the files; the one shown is closed as the next is read. */
  const goTo = (next: number) => {
    if (next === file || next < 0 || next >= targets.length) return
    setFile(next)
    setInspection(null)
    setError(null)
    setBusy(true)
    setStepIndex(null)
    setPlaying(false)
    setMode('')
  }
  /** D130: the structure shown as xyz text on the clipboard; while a mode is animated, the
   * structure the modes belong to. */
  const copyXyz = async () => {
    const chosen = shown[vibration ? count - 1 : at]
    if (!step || !inspection || !chosen) return
    const text = geometryToXyz(chosen.frame.geometry, copiedComment(inspection.name, step, chosen.frame, chosen.n))
    try {
      await navigator.clipboard.writeText(text)
      setCopied(`Copied structure ${chosen.n + 1}`)
    } catch {
      setCopied('The browser did not allow copying')
    }
  }
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(null), 2500)
    return () => clearTimeout(timer)
  }, [copied])

  const close = () => {
    closing.current = true
    if (opened.current) void api.closeInspection(opened.current)
    opened.current = null
    onClose()
  }
  const importIt = () => {
    if (!inspection) return
    // The import goes on with this copy; the window's own copy is closed by the import.
    closing.current = true
    opened.current = null
    onImport(inspection)
  }

  const unit = settings?.energy_unit ?? 'kcal/mol'
  const current = shown[at]?.frame ?? null
  const last = [...shown].reverse().find(({ frame }) => frame.energy !== null)?.frame.energy ?? null
  const title = `Inspect ${inspection?.name ?? baseName(target.path)}`

  return (
    <Modal
      title={title}
      onClose={close}
      wide
      actions={
        <>
          <label className="check" title="Read the file again every 30 seconds while this window is open">
            <input type="checkbox" checked={auto} disabled={!inspection} onChange={(event) => setAuto(event.target.checked)} />
            Every 30 s
          </label>
          <button onClick={refresh} disabled={!inspection || busy} title="Read the file again, for a job still running">
            Refresh
          </button>
          <button
            onClick={importIt}
            disabled={!inspection || !canImport}
            title={canImport ? 'Go on to the import preview with this file' : 'Open an investigation to import into it'}
          >
            Import this file
          </button>
          <button className="primary" onClick={close}>
            Close
          </button>
        </>
      }
    >
      <div className="inspect" aria-label="Inspected file">
        {targets.length > 1 && (
          <div className="inspect-files" role="group" aria-label="Files to inspect">
            <button className="small" disabled={file === 0} onClick={() => goTo(file - 1)} title="The file before">
              ◀ Previous file
            </button>
            <select aria-label="File" value={file} onChange={(event) => goTo(Number(event.target.value))}>
              {targets.map((t, n) => (
                <option key={`${n}:${t.path}`} value={n} title={t.path}>
                  {baseName(t.path)}
                </option>
              ))}
            </select>
            <span className="muted small">
              {file + 1} of {targets.length}
            </span>
            <button
              className="small"
              disabled={file === targets.length - 1}
              onClick={() => goTo(file + 1)}
              title="The file after"
            >
              Next file ▶
            </button>
          </div>
        )}
        {error && <p role="alert">{error}</p>}
        {!inspection && busy && (
          <p className="muted">{target.server ? `Copying ${target.path} …` : `Reading ${target.path} …`}</p>
        )}
        {inspection && (
          <>
            <p className="inspect-origin small">
              <span className="mono">{inspection.path}</span> · {inspection.program}
              {inspection.version ? ` ${inspection.version}` : ''} · {size(inspection.size)} · read at{' '}
              {new Date(inspection.read_at * 1000).toLocaleTimeString()}
              {busy && <span className="muted"> · reading again…</span>}
            </p>
            {inspection.warnings.map((warning) => (
              <p key={warning} className="warning small">
                {warning}
              </p>
            ))}
            <div className="inspect-steps">
              <table className="small" aria-label="Job steps">
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Job</th>
                    <th>Route</th>
                    <th>Charge, mult.</th>
                    <th>Ended</th>
                    <th>Last SCF energy</th>
                    <th>Imaginary</th>
                    <th>Structures</th>
                  </tr>
                </thead>
                <tbody>
                  {inspection.steps.map((s) => (
                    <tr
                      key={s.index}
                      className={s.index === stepIndex ? 'chosen' : undefined}
                      aria-selected={s.index === stepIndex}
                      onClick={() => choose(s.index)}
                    >
                      <td>{s.index}</td>
                      <td>
                        {jobLabel(s)}
                        {s.optimization_converged === true && <span className="criterion-met"> ✓</span>}
                        {s.optimization_converged === false && (
                          <span className="criterion-unmet" title="The optimization has not converged">
                            {' '}
                            ✗
                          </span>
                        )}
                      </td>
                      <td className="mono inspect-route" title={s.route}>
                        {s.route || s.method || '–'}
                      </td>
                      <td>
                        {s.charge ?? '?'}, {s.multiplicity ?? '?'}
                      </td>
                      <td className={s.termination === 'normal' ? undefined : 'criterion-unmet'}>
                        {s.termination === 'normal' ? 'normally' : 'not (yet)'}
                      </td>
                      <td className="mono">{hartree(s.energy)}</td>
                      <td>{s.frequencies.length ? s.imaginary : '–'}</td>
                      <td>{s.frames.length || '–'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {step && step.missing.length > 0 && (
              <p className="muted small">Not in the file: {step.missing.join('; ')}.</p>
            )}
            {step && frames.length === 0 && (
              <p className="muted">
                This step prints no structure{step.job_type !== 'single_point' && inspection.program === 'xTB'
                  ? '; xTB writes each optimization cycle to xtbopt.log, which can be inspected for the movie'
                  : ''}
                .
              </p>
            )}
            {step && count > 0 && (
              <div className="inspect-view">
                <Viewer3D
                  models={[{ xyz: xyzFrames[count - 1] }]}
                  trajectory={vibration ? null : { frames: xyzFrames, index: at }}
                  vibration={vibration}
                />
                <div className="inspect-side step-movie">
                  {modes && (
                    <select aria-label="Animate mode" value={mode} onChange={(event) => setMode(event.target.value)}>
                      <option value="">No mode animation</option>
                      {modes.order.map((n) => (
                        <option key={n} value={n}>
                          Mode {n + 1}: {frequency(modes.frequencies[n])} cm⁻¹
                        </option>
                      ))}
                    </select>
                  )}
                  {count > 1 && !vibration && (
                    <div className="step-controls">
                      <button className="small" aria-label="Previous structure" onClick={() => move(-1)}>
                        ◀
                      </button>
                      <button
                        className="small"
                        aria-label={playing ? 'Pause' : 'Play'}
                        onClick={() => setPlaying(!playing)}
                      >
                        {playing ? '❚❚' : '▶'}
                      </button>
                      <button className="small" aria-label="Next structure" onClick={() => move(1)}>
                        ▶|
                      </button>
                      <input
                        type="range"
                        aria-label="Structure"
                        min={0}
                        max={count - 1}
                        value={at}
                        onChange={(event) => {
                          setPlaying(false)
                          setIndex(Number(event.target.value))
                        }}
                      />
                      <select aria-label="Speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
                        {SPEEDS.map((s) => (
                          <option key={s} value={s}>
                            {s}/s
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div className="inspect-copy">
                    <button
                      className="small"
                      onClick={() => void copyXyz()}
                      title={
                        vibration
                          ? 'Copy the coordinates of the structure at rest, as xyz text'
                          : 'Copy the coordinates of the structure shown, as xyz text'
                      }
                    >
                      Copy XYZ
                    </button>
                    {copied && (
                      <span className="muted small" role="status">
                        {copied}
                      </span>
                    )}
                  </div>
                  {filterable && !vibration && (
                    <label className="check" title="One structure per scan point: the one its optimization converged on">
                      <input
                        type="checkbox"
                        checked={convergedOnly}
                        onChange={(event) => showConverged(event.target.checked)}
                      />
                      Converged points only
                    </label>
                  )}
                  {!vibration && (
                    <p className="step-info small" aria-label="Structure shown">
                      Structure {(shown[at]?.n ?? 0) + 1} of {frames.length}
                      {current?.point !== null && current?.point !== undefined && ` · scan point ${current.point} of ${step.points}`}
                      {current?.converged && ' · converged'}
                      {' · '}
                      <span className="mono">E = {hartree(current?.energy)}</span>
                      {current?.energy !== null && current?.energy !== undefined && last !== null && count > 1 && (
                        <>
                          {' · '}
                          <span className="mono">
                            ΔE = {formatDelta(current.energy - last, settings)} {unit}
                          </span>{' '}
                          <span className="muted">to the last structure</span>
                        </>
                      )}
                    </p>
                  )}
                  {!vibration && (
                    <>
                      <EnergyChart
                        items={shown.map(({ frame, n }) => ({ n, energy: frame.energy, removed: false, spike: false }))}
                        cuts={[]}
                        at={at}
                        last={last}
                        unit={unit}
                        settings={settings}
                        selected={null}
                        onPick={(i) => {
                          setPlaying(false)
                          setIndex(i)
                        }}
                      />
                      <Convergence
                        items={shown.map(({ frame, n }) => ({ n, criteria: frame.criteria }))}
                        at={at}
                        onPick={(i) => {
                          setPlaying(false)
                          setIndex(i)
                        }}
                      />
                    </>
                  )}
                </div>
              </div>
            )}
            <p className="muted small">
              Read without importing: nothing is written to the investigation
              {inspection.server_name ? ` or on ${inspection.server_name}; the copy is deleted when this window closes` : ''}.
            </p>
          </>
        )}
      </div>
    </Modal>
  )
}
