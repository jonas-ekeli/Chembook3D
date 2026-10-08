import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { api, CALCULATION_TYPES, formatDelta, geometryToXyz, type Calculation, type Node, type Settings, type Steps } from '../api'
import { hartree } from '../util'

export type Movie = { frames: string[]; index: number }

const SPEEDS = [1, 2, 5, 10, 20, 30]

/** D112: an xTB relaxed scan read from `xtbscan.log` or `path.xyz` (`xtb.SCAN_ROUTE`). */
const isScanPath = (c: Calculation) => c.program === 'xTB' && c.route.startsWith('relaxed scan')

/** D101, FR-3D-08: play the structures of one of the node's optimizations or scans in its 3D
 * view. `onShow` gives the viewer the structures and the one to show (null: the node's own
 * structure); `onStart` is called when a movie is chosen, so a running vibration stops, and a
 * vibration being shown (`vibrating`) stops the movie. "Use this structure" (D112) makes the
 * structure shown the node's geometry, in place on a scan path node and otherwise on a new
 * derived node, and hands the result to `onUsed`. */
export function StepMovie({
  nodeId,
  refreshKey,
  settings,
  vibrating,
  onShow,
  onStart,
  onUsed,
}: {
  nodeId: string
  refreshKey: number
  settings: Settings | null
  vibrating: boolean
  onShow: (movie: Movie | null) => void
  onStart: () => void
  onUsed: (node: Node, derived: boolean, structure: number) => void
}) {
  const [calcs, setCalcs] = useState<Calculation[]>([])
  const [calcId, setCalcId] = useState('')
  const [steps, setSteps] = useState<Steps | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [convergedOnly, setConvergedOnly] = useState(true)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(5)
  const [using, setUsing] = useState(false)

  useEffect(() => {
    api.calculations(nodeId).then(
      (all) => setCalcs(all.filter((c) => (c.result?.geometry_count ?? 0) > 1 && c.source_file)),
      () => setCalcs([]),
    )
  }, [nodeId, refreshKey])

  useEffect(() => {
    if (vibrating) setCalcId('')
  }, [vibrating])

  useEffect(() => {
    setSteps(null)
    setError(null)
    setIndex(0)
    setPlaying(false)
    if (!calcId) return
    let cancelled = false
    api.steps(calcId).then(
      (loaded) => {
        if (cancelled) return
        setSteps(loaded)
        setConvergedOnly(loaded.scan !== null)
        setPlaying(true)
      },
      (err: unknown) => !cancelled && setError(err instanceof Error ? err.message : String(err)),
    )
    return () => {
      cancelled = true
    }
  }, [calcId])

  // The structures shown: for a scan, optionally only the converged one of each point.
  const shown = useMemo(() => {
    if (!steps) return []
    const all = steps.frames.map((frame, n) => ({ frame, n }))
    return steps.scan && convergedOnly ? all.filter(({ frame }) => frame.converged) : all
  }, [steps, convergedOnly])
  // Every structure of an xTB scan is a converged point: nothing to filter (A58).
  const filterable = steps !== null && steps.scan !== null && steps.frames.some((frame) => !frame.converged)
  const stages = steps ? new Set(steps.frames.map((frame) => frame.stage).filter((s) => s !== null)).size : 0
  const xyzFrames = useMemo(
    () => shown.map(({ frame, n }) => geometryToXyz(frame.geometry, `structure ${n + 1}`)),
    [shown],
  )
  const count = shown.length
  const at = Math.min(index, Math.max(count - 1, 0))

  useEffect(() => {
    onShow(count > 0 ? { frames: xyzFrames, index: at } : null)
    // onShow is a state setter from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xyzFrames, at, count])

  useEffect(() => {
    if (!playing || count < 2) return
    const timer = setInterval(() => setIndex((i) => (Math.min(i, count - 1) + 1) % count), 1000 / speed)
    return () => clearInterval(timer)
  }, [playing, speed, count])

  if (calcs.length === 0) return null

  const current = shown[at]?.frame
  const last = [...shown].reverse().find(({ frame }) => frame.energy !== null)?.frame.energy ?? null
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const step = (by: number) => {
    setPlaying(false)
    setIndex((at + by + count) % count)
  }
  const choose = (id: string) => {
    setCalcId(id)
    if (id) onStart()
  }
  const use = () => {
    const n = shown[at]?.n
    if (n === undefined) return
    setPlaying(false)
    setUsing(true)
    setError(null)
    api.useStep(calcId, n).then(
      (result) => {
        setUsing(false)
        onUsed(result.node, result.derived, n + 1)
      },
      (err: unknown) => {
        setUsing(false)
        setError(err instanceof Error ? err.message : String(err))
      },
    )
  }

  return (
    <div className="step-movie" aria-label="Optimization steps">
      <select aria-label="Steps of" value={calcId} onChange={(event) => choose(event.target.value)}>
        <option value="">No step movie</option>
        {calcs.map((c) => (
          <option key={c.id} value={c.id}>
            Steps: {isScanPath(c) ? 'Relaxed scan' : (CALCULATION_TYPES[c.type] ?? c.type)}, {c.composite_label} ({c.result?.geometry_count} structures)
          </option>
        ))}
      </select>
      {error && <p role="alert">{error}</p>}
      {steps && count > 0 && (
        <>
          <div className="step-controls">
            <button className="small" aria-label="Previous structure" title="Previous structure" onClick={() => step(-1)}>
              ◀
            </button>
            <button
              className="small"
              aria-label={playing ? 'Pause' : 'Play'}
              title={playing ? 'Pause' : 'Play'}
              onClick={() => setPlaying(!playing)}
              disabled={count < 2}
            >
              {playing ? '❚❚' : '▶'}
            </button>
            <button className="small" aria-label="Next structure" title="Next structure" onClick={() => step(1)}>
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
            {filterable && (
              <label className="check" title="One structure per scan point: the one its optimization converged on">
                <input
                  type="checkbox"
                  checked={convergedOnly}
                  onChange={(event) => {
                    setConvergedOnly(event.target.checked)
                    setIndex(0)
                  }}
                />
                Converged points only
              </label>
            )}
          </div>
          <p className="step-info small" aria-label="Structure shown">
            Structure {shown[at].n + 1} of {steps.frames.length}
            {current?.point !== null && current?.point !== undefined && (
              <>
                {' '}
                · scan point {current.point} of {steps.points}
              </>
            )}
            {current?.stage !== null && current?.stage !== undefined && stages > 1 && (
              <>
                {' '}
                · stage {current.stage} of {stages}
              </>
            )}
            {current?.converged && ' · converged'}
            {' · '}
            <span className="mono">E = {hartree(current?.energy)}</span>
            {current?.energy !== null && current?.energy !== undefined && last !== null && (
              <>
                {' · '}
                <span className="mono">
                  ΔE = {formatDelta(current.energy - last, settings)} {unit}
                </span>{' '}
                <span className="muted">to the last structure</span>
              </>
            )}
          </p>
          <div className="step-use">
            <button
              className="small"
              onClick={use}
              disabled={using}
              title={
                steps.in_place
                  ? "Make the structure shown this node's geometry"
                  : 'This node has calculations on its own geometry, so the structure shown becomes a new node derived from it'
              }
            >
              Use this structure
            </button>{' '}
            <span className="muted small">{steps.in_place ? 'changes this node' : 'makes a derived node'}</span>
          </div>
          <EnergyChart
            energies={shown.map(({ frame }) => frame.energy)}
            at={at}
            last={last}
            unit={unit}
            settings={settings}
            onPick={(i) => {
              setPlaying(false)
              setIndex(i)
            }}
          />
        </>
      )}
    </div>
  )
}

/** ΔE of each structure shown, with the shown one marked; a click shows the nearest one. */
function EnergyChart({
  energies,
  at,
  last,
  unit,
  settings,
  onPick,
}: {
  energies: (number | null)[]
  at: number
  last: number | null
  unit: string
  settings: Settings | null
  onPick: (index: number) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const known = energies.filter((e): e is number => e !== null)
  if (known.length < 2 || last === null) return null
  const width = 300
  const height = 64
  const pad = 4
  const low = Math.min(...known)
  const high = Math.max(...known)
  const span = high - low || 1
  const x = (i: number) => pad + (energies.length > 1 ? (i / (energies.length - 1)) * (width - 2 * pad) : 0)
  const y = (e: number) => pad + ((high - e) / span) * (height - 2 * pad)
  const points = energies
    .map((e, i) => (e === null ? null : `${x(i).toFixed(1)},${y(e).toFixed(1)}`))
    .filter(Boolean)
    .join(' ')
  const shown = energies[at]
  const pick = (event: MouseEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect()
    if (!box || energies.length < 2) return
    const fraction = ((event.clientX - box.left) / box.width) * width
    const i = Math.round(((fraction - pad) / (width - 2 * pad)) * (energies.length - 1))
    onPick(Math.max(0, Math.min(energies.length - 1, i)))
  }
  return (
    <div className="step-chart">
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Energy per structure"
        onClick={pick}
      >
        <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        <line x1={x(at)} x2={x(at)} y1={0} y2={height} stroke="var(--muted)" strokeWidth={1} vectorEffect="non-scaling-stroke" />
        {shown !== null && shown !== undefined && (
          <line
            x1={x(at)}
            x2={x(at)}
            y1={y(shown) - 4}
            y2={y(shown) + 4}
            stroke="var(--accent)"
            strokeWidth={4}
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
      <div className="step-chart-scale muted small">
        <span>
          {formatDelta(high - last, settings)} {unit}
        </span>
        <span>
          {formatDelta(low - last, settings)} {unit}
        </span>
      </div>
    </div>
  )
}
