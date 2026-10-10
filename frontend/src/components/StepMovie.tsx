import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import {
  api,
  CALCULATION_TYPES,
  formatDelta,
  geometryToXyz,
  type Calculation,
  type Node,
  type Settings,
  type StepFrame,
  type Steps,
  type Trim,
} from '../api'
import { hartree, ranges } from '../util'

export type Movie = { frames: string[]; index: number }

const SPEEDS = [1, 2, 5, 10, 20, 30]

/** D112: an xTB relaxed scan read from `xtbscan.log` or `path.xyz` (`xtb.SCAN_ROUTE`). */
const isScanPath = (c: Calculation) => c.program === 'xTB' && c.route.startsWith('relaxed scan')

type Shown = { frame: StepFrame; n: number }

/** The structures the movie plays: for a scan, optionally only the converged one of each point;
 * for a scan path, without the points removed from it unless they are asked for (D117). */
function visible(steps: Steps | null, convergedOnly: boolean, showRemoved: boolean): Shown[] {
  if (!steps) return []
  let all = steps.frames.map((frame, n) => ({ frame, n }))
  if (steps.trim && !showRemoved) all = all.filter(({ frame }) => !frame.removed)
  return steps.scan && convergedOnly ? all.filter(({ frame }) => frame.converged) : all
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`

/** D101, FR-3D-08: play the structures of one of the node's optimizations or scans in its 3D
 * view. `onShow` gives the viewer the structures and the one to show (null: the node's own
 * structure); `onStart` is called when a movie is chosen, so a running vibration stops, and a
 * vibration being shown (`vibrating`) stops the movie. "Use this structure" (D112) makes the
 * structure shown the node's geometry, in place on a scan path node and otherwise on a new
 * derived node, and hands the result to `onUsed`. On a scan path, points can be removed from
 * the path and put back (D117); `onTrimmed` gets the node, which may have moved to the new top. */
export function StepMovie({
  nodeId,
  calculationId = null,
  refreshKey,
  settings,
  vibrating,
  onShow,
  onStart,
  onUsed,
  onTrimmed,
}: {
  nodeId: string
  /** D121: the calculation whose movie starts by itself (the edge panel's scan path). */
  calculationId?: string | null
  refreshKey: number
  settings: Settings | null
  vibrating: boolean
  onShow: (movie: Movie | null) => void
  onStart: () => void
  onUsed: (node: Node, derived: boolean, structure: number) => void
  onTrimmed: (node: Node, notice: string) => void
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
  // D117: the removed points shown faded, a range of frames chosen on the chart (inclusive frame
  // indices) and what removing it would leave.
  const [showRemoved, setShowRemoved] = useState(false)
  const [selection, setSelection] = useState<{ from: number; to: number } | null>(null)
  const [preview, setPreview] = useState<Trim | string | null>(null)

  useEffect(() => {
    api.calculations(nodeId).then(
      (all) => setCalcs(all.filter((c) => (c.result?.geometry_count ?? 0) > 1 && c.source_file)),
      () => setCalcs([]),
    )
  }, [nodeId, refreshKey])

  useEffect(() => {
    if (vibrating) setCalcId('')
  }, [vibrating])

  // D121: start on the calculation asked for, once its list has loaded.
  const [started, setStarted] = useState<string | null>(null)
  useEffect(() => {
    if (calculationId && started !== calculationId && calcs.some((c) => c.id === calculationId)) {
      setStarted(calculationId)
      setCalcId(calculationId)
    }
  }, [calculationId, calcs, started])

  useEffect(() => {
    setSteps(null)
    setError(null)
    setIndex(0)
    setPlaying(false)
    setShowRemoved(false)
    setSelection(null)
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

  const shown = useMemo(() => visible(steps, convergedOnly, showRemoved), [steps, convergedOnly, showRemoved])
  // Every structure of an xTB scan is a converged point: nothing to filter (A58).
  const filterable = steps !== null && steps.scan !== null && steps.frames.some((frame) => !frame.converged)
  const stages = steps ? new Set(steps.frames.map((frame) => frame.stage).filter((s) => s !== null)).size : 0
  const xyzFrames = useMemo(
    () => shown.map(({ frame, n }) => geometryToXyz(frame.geometry, `structure ${n + 1}`)),
    [shown],
  )
  const count = shown.length
  const at = Math.min(index, Math.max(count - 1, 0))
  const trim = steps?.trim ?? null

  // The points a selection would remove, and those it would put back.
  const selected = useMemo(() => {
    if (!steps || !selection) return null
    const inside = steps.frames.filter((_, n) => n >= selection.from && n <= selection.to)
    const points = (removed: boolean) =>
      inside.filter((f) => f.removed === removed && f.point !== null).map((f) => f.point as number)
    return { remove: points(false), restore: points(true) }
  }, [steps, selection])

  useEffect(() => {
    setPreview(null)
    if (!calcId || !selected || selected.remove.length === 0) return
    let cancelled = false
    api.previewTrim(calcId, { remove: selected.remove }).then(
      (result) => !cancelled && setPreview(result),
      (err: unknown) => !cancelled && setPreview(err instanceof Error ? err.message : String(err)),
    )
    return () => {
      cancelled = true
    }
  }, [calcId, selected])

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
  const last = [...shown].reverse().find(({ frame }) => frame.energy !== null && !frame.removed)?.frame.energy ?? null
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const pointOf = (n: number) => steps?.frames[n]?.point ?? n + 1
  const step = (by: number) => {
    setPlaying(false)
    setIndex((at + by + count) % count)
  }
  const choose = (id: string) => {
    setCalcId(id)
    if (id) onStart()
  }
  const failed = (err: unknown) => {
    setUsing(false)
    setError(err instanceof Error ? err.message : String(err))
  }
  const use = (n: number | undefined = shown[at]?.n) => {
    if (n === undefined) return
    setPlaying(false)
    setUsing(true)
    setError(null)
    api.useStep(calcId, n).then((result) => {
      setUsing(false)
      onUsed(result.node, result.derived, n + 1)
      // A scan path's movie says which point the node stands for (D117).
      if (trim) api.steps(calcId).then(setSteps, () => undefined)
    }, failed)
  }
  /** D117: takes points out of the path or puts them back, keeping the structure shown. */
  const change = (body: { remove?: number[]; restore?: number[] }, notice: string) => {
    const keep = shown[at]?.n ?? 0
    setPlaying(false)
    setUsing(true)
    setError(null)
    api.trimSteps(calcId, body).then((result) => {
      setUsing(false)
      setSelection(null)
      setSteps(result.steps)
      const next = visible(result.steps, convergedOnly, showRemoved)
      const position = next.findIndex(({ n }) => n >= keep)
      setIndex(position >= 0 ? position : Math.max(next.length - 1, 0))
      const moved =
        result.moved !== null ? ` The node now shows the new top, structure ${result.moved + 1}.` : ''
      onTrimmed(result.node, notice + moved)
    }, failed)
  }
  const select = (a: number, b: number) => {
    const from = shown[Math.min(a, b)]?.n
    const to = shown[Math.max(a, b)]?.n
    if (from === undefined || to === undefined) return
    setPlaying(false)
    setSelection({ from, to })
  }
  const selectedAt =
    selection && count > 0
      ? ([
          shown.findIndex(({ n }) => n >= selection.from),
          shown.length - 1 - [...shown].reverse().findIndex(({ n }) => n <= selection.to),
        ] as [number, number])
      : null

  return (
    <div className="step-movie" aria-label="Optimization steps">
      <select aria-label="Steps of" value={calcId} onChange={(event) => choose(event.target.value)}>
        <option value="">No step movie</option>
        {calcs.map((c) => (
          <option key={c.id} value={c.id}>
            Steps: {isScanPath(c) ? 'Relaxed scan' : (CALCULATION_TYPES[c.type] ?? c.type)}, {c.composite_label} ({c.result?.geometry_count} structures
            {c.removed_points.length > 0 && `, ${c.removed_points.length} removed`})
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
            {trim && trim.removed.length > 0 && (
              <label className="check" title="Show the removed points faded, to look at them or put them back">
                <input
                  type="checkbox"
                  checked={showRemoved}
                  onChange={(event) => {
                    const keep = shown[at]?.n ?? 0
                    const next = visible(steps, convergedOnly, event.target.checked)
                    setShowRemoved(event.target.checked)
                    setIndex(Math.max(next.findIndex(({ n }) => n >= keep), 0))
                  }}
                />
                Show removed
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
            {current?.removed && ' · removed from the path'}
            {' · '}
            <span className="mono">E = {hartree(current?.energy)}</span>
            {current?.energy !== null && current?.energy !== undefined && last !== null && (
              <>
                {' · '}
                <span className="mono">
                  ΔE = {formatDelta(current.energy - last, settings)} {unit}
                </span>{' '}
                <span className="muted">to the last {trim && trim.removed.length > 0 ? 'kept ' : ''}structure</span>
              </>
            )}
          </p>
          <div className="step-use">
            <button
              className="small"
              onClick={() => use()}
              disabled={using || current?.removed}
              title={
                steps.in_place
                  ? "Make the structure shown this node's geometry"
                  : 'This node has calculations on its own geometry, so the structure shown becomes a new node derived from it'
              }
            >
              Use this structure
            </button>{' '}
            {trim && current && current.point !== null && !current.removed && (
              <>
                <button
                  className="small"
                  disabled={using || trim.kept <= 2}
                  title="Leave this point out of the path; the file is not changed and it can be put back"
                  onClick={() => change({ remove: [current.point!] }, `Point ${current.point} removed from the path.`)}
                >
                  Remove this point
                </button>{' '}
              </>
            )}
            {trim && current && current.point !== null && current.removed && (
              <>
                <button
                  className="small"
                  disabled={using}
                  onClick={() => change({ restore: [current.point!] }, `Point ${current.point} put back in the path.`)}
                >
                  Restore this point
                </button>{' '}
              </>
            )}
            <span className="muted small">{steps.in_place ? 'changes this node' : 'makes a derived node'}</span>
          </div>
          {trim && (
            <TrimPanel
              steps={steps}
              trim={trim}
              settings={settings}
              unit={unit}
              busy={using}
              pointOf={pointOf}
              selection={selection}
              selected={selected}
              preview={preview}
              onChange={change}
              onUse={use}
              onClear={() => setSelection(null)}
            />
          )}
          <EnergyChart
            items={shown.map(({ frame, n }) => ({
              n,
              energy: frame.energy,
              removed: frame.removed,
              spike: trim?.spikes.includes(n) ?? false,
            }))}
            cuts={trim?.cuts ?? []}
            at={at}
            last={last}
            unit={unit}
            settings={settings}
            selected={selectedAt}
            onPick={(i) => {
              setPlaying(false)
              setIndex(i)
            }}
            onSelect={trim ? select : undefined}
          />
          {trim && (
            <p className="muted small step-chart-hint">
              Drag across the chart, or shift-click a second point, to choose a section.
            </p>
          )}
        </>
      )}
    </div>
  )
}

/** D117: what has been removed from a scan path, what the path looks like without it, and
 * the actions on a section chosen on the chart. */
function TrimPanel({
  steps,
  trim,
  settings,
  unit,
  busy,
  pointOf,
  selection,
  selected,
  preview,
  onChange,
  onUse,
  onClear,
}: {
  steps: Steps
  trim: Trim
  settings: Settings | null
  unit: string
  busy: boolean
  pointOf: (n: number) => number
  selection: { from: number; to: number } | null
  selected: { remove: number[]; restore: number[] } | null
  preview: Trim | string | null
  onChange: (body: { remove?: number[]; restore?: number[] }, notice: string) => void
  onUse: (n: number) => void
  onClear: () => void
}) {
  const total = steps.frames.length
  const spikes = trim.spikes.map(pointOf)
  const shownRemoved = trim.shown !== null && steps.frames[trim.shown]?.removed
  const cut = (c: Trim['cuts'][number]) =>
    `${c.jump === null ? 'n/a' : `${c.jump.toFixed(2)} Å`} between points ${pointOf(c.before)} and ${pointOf(c.after)}`
  // The cut a selection would leave: the one spanning it in the preview.
  const leaves =
    preview && typeof preview !== 'string' && selection
      ? preview.cuts.find((c) => c.before < selection.from && c.after > selection.to)
      : undefined
  return (
    <div className="trim-panel" aria-label="Trimmed path">
      {trim.removed.length > 0 && (
        <p className="small">
          {plural(trim.removed.length, 'point')} of {total} removed ({trim.removed.length === 1 ? 'point' : 'points'}{' '}
          {ranges(trim.removed)}). Top: point{' '}
          {pointOf(trim.top)}
          {trim.barrier !== null && (
            <>
              ,{' '}
              <span className="mono">
                {formatDelta(trim.barrier, settings)} {unit}
              </span>{' '}
              above the first kept point ({pointOf(steps.frames.findIndex((f) => !f.removed))})
            </>
          )}
          .{' '}
          {trim.cuts.length > 0 && <>Jumps across the cuts: {trim.cuts.map(cut).join('; ')}. </>}
          <button
            className="small"
            disabled={busy}
            onClick={() => onChange({ restore: trim.removed }, 'Every point is back in the path.')}
          >
            Restore all
          </button>
        </p>
      )}
      {trim.cuts
        .filter((c) => c.large)
        .map((c) => (
          <p key={c.before} className="notice warn small" role="note">
            The structure jumps {cut(c)}, over 0.5 Å: the trimmed path is not continuous there, and its top may be
            too low. Running that section again is the proper fix.
          </p>
        ))}
      {(trim.first_removed || trim.last_removed) && (
        <p className="notice warn small" role="note">
          The path no longer {trim.first_removed && trim.last_removed ? 'starts or ends' : trim.first_removed ? 'starts' : 'ends'} at
          the structure{trim.first_removed && trim.last_removed ? 's' : ''} it was run{' '}
          {trim.first_removed && trim.last_removed ? 'between' : trim.first_removed ? 'from' : 'to'}.
        </p>
      )}
      {shownRemoved && trim.shown !== null && (
        <p className="notice warn small" role="note">
          This node shows point {pointOf(trim.shown)}, which is removed from the path.{' '}
          <button className="small" disabled={busy} onClick={() => onUse(trim.top)}>
            Use the new top (point {pointOf(trim.top)})
          </button>
        </p>
      )}
      {spikes.length > 0 && (
        <p className="small">
          {spikes.length === 1 ? 'Point' : 'Points'} {ranges(spikes)} {spikes.length === 1 ? 'is a spike' : 'are spikes'}: over
          10 kcal/mol above both neighbours, with a jump over 0.5 Å.{' '}
          <button
            className="small"
            disabled={busy || trim.kept - spikes.length < 2}
            onClick={() =>
              onChange(
                { remove: spikes },
                `${spikes.length === 1 ? 'Spike' : 'Spikes'} at ${spikes.length === 1 ? 'point' : 'points'} ${ranges(spikes)} removed from the path.`,
              )
            }
          >
            Remove {spikes.length === 1 ? 'the spike' : `${spikes.length} spikes`}
          </button>
        </p>
      )}
      {selection && selected && (
        <div className="trim-selection small" aria-label="Chosen section">
          <span>
            Points {pointOf(selection.from)}–{pointOf(selection.to)}
            {selected.remove.length > 0 && <>: {plural(selected.remove.length, 'point')} to remove</>}
            {typeof preview === 'string' && <span className="error"> {preview}</span>}
            {leaves && (
              <>
                , leaving a jump of {cut(leaves)}
                {leaves.large && <strong> (over 0.5 Å)</strong>}
              </>
            )}
            {preview && typeof preview !== 'string' && (
              <>
                ; the top would be point {pointOf(preview.top)}
                {preview.first_removed && !trim.first_removed && '; the path would no longer start at its first point'}
                {preview.last_removed && !trim.last_removed && '; the path would no longer end at its last point'}
              </>
            )}
            .
          </span>{' '}
          {selected.remove.length > 0 && (
            <button
              className="small"
              disabled={busy || typeof preview === 'string'}
              onClick={() =>
                onChange({ remove: selected.remove }, `Points ${ranges(selected.remove)} removed from the path.`)
              }
            >
              Remove
            </button>
          )}{' '}
          {selected.restore.length > 0 && (
            <button
              className="small"
              disabled={busy}
              onClick={() =>
                onChange({ restore: selected.restore }, `Points ${ranges(selected.restore)} put back in the path.`)
              }
            >
              Restore
            </button>
          )}{' '}
          <button className="small" onClick={onClear}>
            Clear
          </button>
        </div>
      )}
    </div>
  )
}

type ChartItem = { n: number; energy: number | null; removed: boolean; spike: boolean }

/** ΔE of each structure shown, with the shown one marked; a click shows the nearest one. On a
 * scan path (D117) removed points are hollow and grey, a cut is a dashed line (red over 0.5 Å),
 * spikes are marked, and dragging across the chart (or shift-clicking) chooses a section. */
function EnergyChart({
  items,
  cuts,
  at,
  last,
  unit,
  settings,
  selected,
  onPick,
  onSelect,
}: {
  items: ChartItem[]
  cuts: Trim['cuts']
  at: number
  last: number | null
  unit: string
  settings: Settings | null
  selected: [number, number] | null
  onPick: (index: number) => void
  onSelect?: (from: number, to: number) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef<{ start: number; moved: boolean } | null>(null)
  const known = items.map((item) => item.energy).filter((e): e is number => e !== null)
  if (known.length < 2 || last === null) return null
  const width = 300
  const height = 64
  const pad = 4
  const low = Math.min(...known)
  const high = Math.max(...known)
  const span = high - low || 1
  const x = (i: number) => pad + (items.length > 1 ? (i / (items.length - 1)) * (width - 2 * pad) : 0)
  const y = (e: number) => pad + ((high - e) / span) * (height - 2 * pad)
  const xy = (i: number) => `${x(i).toFixed(1)},${y(items[i].energy!).toFixed(1)}`

  // Solid runs between kept points next to each other in the file; a dashed line across a cut.
  const cutAfter = new Map(cuts.map((c) => [c.before, c]))
  const kept = items.map((item, i) => ({ ...item, i })).filter((item) => !item.removed && item.energy !== null)
  const runs: string[][] = []
  const gaps: { from: number; to: number; large: boolean }[] = []
  kept.forEach((item, k) => {
    const previous = kept[k - 1]
    const gap = previous ? cutAfter.get(previous.n) : undefined
    if (previous && gap && gap.after === item.n) {
      gaps.push({ from: previous.i, to: item.i, large: gap.large })
      runs.push([xy(item.i)])
    } else if (runs.length === 0) runs.push([xy(item.i)])
    else runs[runs.length - 1].push(xy(item.i))
  })

  const indexAt = (clientX: number) => {
    const box = svg.current?.getBoundingClientRect()
    if (!box || items.length < 2) return 0
    const fraction = ((clientX - box.left) / box.width) * width
    const i = Math.round(((fraction - pad) / (width - 2 * pad)) * (items.length - 1))
    return Math.max(0, Math.min(items.length - 1, i))
  }
  const down = (event: PointerEvent<SVGSVGElement>) => {
    drag.current = { start: indexAt(event.clientX), moved: false }
    svg.current?.setPointerCapture?.(event.pointerId)
  }
  const move = (event: PointerEvent<SVGSVGElement>) => {
    const current = drag.current
    if (!current || !onSelect) return
    const i = indexAt(event.clientX)
    if (i !== current.start || current.moved) {
      current.moved = true
      onSelect(current.start, i)
    }
  }
  const up = (event: PointerEvent<SVGSVGElement>) => {
    const current = drag.current
    drag.current = null
    if (!current || current.moved) return
    const i = indexAt(event.clientX)
    if (event.shiftKey && onSelect) onSelect(at, i)
    else onPick(i)
  }
  // A dot whatever the chart's stretch: a zero-length line with round caps.
  const dot = (i: number, colour: string, size: number) => (
    <line
      x1={x(i)}
      x2={x(i)}
      y1={y(items[i].energy!)}
      y2={y(items[i].energy!)}
      stroke={colour}
      strokeWidth={size}
      strokeLinecap="round"
      vectorEffect="non-scaling-stroke"
    />
  )
  const shown = items[at]?.energy
  return (
    <div className="step-chart">
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Energy per structure"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
      >
        {selected && (
          <rect
            x={x(selected[0]) - 2}
            width={Math.max(x(selected[1]) - x(selected[0]) + 4, 4)}
            y={0}
            height={height}
            fill="var(--accent)"
            opacity={0.15}
          />
        )}
        {runs.map((run, k) =>
          run.length > 1 ? (
            <polyline
              key={k}
              points={run.join(' ')}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          ) : null,
        )}
        {gaps.map((gap) => (
          <line
            key={gap.from}
            className="step-chart-cut"
            x1={x(gap.from)}
            x2={x(gap.to)}
            y1={y(items[gap.from].energy!)}
            y2={y(items[gap.to].energy!)}
            stroke={gap.large ? 'var(--danger)' : 'var(--muted)'}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {items.map((item, i) =>
          item.energy === null ? null : item.removed ? (
            <g key={item.n} className="step-chart-removed">
              {dot(i, 'var(--muted)', 6)}
              {dot(i, 'var(--bg)', 3)}
            </g>
          ) : item.spike ? (
            <g key={item.n} className="step-chart-spike">
              {dot(i, 'var(--danger)', 6)}
            </g>
          ) : null,
        )}
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
