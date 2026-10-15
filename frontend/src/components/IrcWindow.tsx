import { useCallback, useEffect, useMemo, useState, type MouseEvent } from 'react'
import {
  api,
  ApiError,
  formatDelta,
  geometryToXyz,
  type GeometryRows,
  type Irc,
  type IrcDirection,
  type IrcPart,
  type IrcPoint,
  type IrcSide,
  type Node,
  type Settings,
} from '../api'
import { ImportDialog } from './ImportDialog'
import { Modal } from './Modal'
import { Viewer3D } from './Viewer3D'

// D131: a TS node's IRC, kept apart from the node: its path in each direction and the
// optimization and frequency job of each end point, imported, inspected and removed here. The
// chart joins both directions through the TS against the distance along the path in
// mass-weighted coordinates, which the app computes from the points for every program; the
// energies are each file's own, relative to its TS point. The end points' energies are
// relative to the TS node at their level and never feed profiles or tables (A76).

const DIRECTIONS: IrcDirection[] = ['reverse', 'forward'] // left to right
const SPEEDS = [2, 5, 10, 20]
const TYPES = ['E', 'G', 'G_qh'] as const

const SIDE_NAMES: Record<IrcDirection, string> = { reverse: 'Reverse', forward: 'Forward' }

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.blockers) return err.blockers.join('; ')
  return err instanceof Error ? err.message : String(err)
}

/** How a direction of an IRC ended, as the program said. */
function endingText(ending: string | null | undefined, maxPoints: number | null): string {
  switch (ending) {
    case 'minimum':
      return 'reached a minimum'
    case 'max_points':
      return `stopped at its maximum of ${maxPoints ?? '?'} points`
    case 'complete':
      return 'ended'
    case 'stopped':
      return 'did not finish'
    default:
      return 'not imported'
  }
}

function pointName(point: IrcPoint): string {
  return point.side === 'ts' ? 'TS' : `${SIDE_NAMES[point.side]} point ${point.number}`
}

/** The comment line of a copied point (as D130). */
function copiedComment(label: string, point: IrcPoint): string {
  const parts = [`${label} IRC`, pointName(point), point.file, `s = ${point.coordinate.toFixed(3)} amu^1/2 bohr`]
  if (point.energy !== null) parts.push(`E = ${point.energy} Eh`)
  return parts.join(', ')
}

/** Round axis steps: 1, 2 or 5 times a power of ten. */
function ticks(low: number, high: number, count = 5): number[] {
  const span = high - low || 1
  const raw = span / count
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw
  const found: number[] = []
  for (let v = Math.ceil(low / step) * step; v <= high + step * 1e-9; v += step) found.push(Math.abs(v) < step * 1e-9 ? 0 : v)
  return found
}

type EndMark = { direction: IrcDirection; x: number; y: number; from: IrcPoint; title: string }

/** The path through the TS: x the distance along it, y ΔE in the display unit. The TS and both
 * IRC ends are marked; an optimized end point is a separate mark beyond its end. */
function IrcChart({
  points,
  ends,
  at,
  settings,
  onPick,
}: {
  points: IrcPoint[]
  ends: { direction: IrcDirection; relative: number; title: string }[]
  at: number
  settings: Settings | null
  onPick: (index: number) => void
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const factor = settings?.energy_factors[unit] ?? 627.5094740631
  const known = points.filter((p) => p.relative !== null)
  if (known.length < 2) return <p className="muted small">The path has too few points with an energy to draw.</p>
  const width = 640
  const height = 240
  const left = 56
  const right = 16
  const top = 14
  const bottom = 40
  const xs = points.map((p) => p.coordinate)
  const lowX = Math.min(...xs)
  const highX = Math.max(...xs)
  const spanX = highX - lowX || 1
  const marks: EndMark[] = []
  for (const end of ends) {
    const side = points.filter((p) => p.side === end.direction)
    const last = end.direction === 'reverse' ? side[0] : side[side.length - 1]
    if (!last) continue
    const sign = end.direction === 'reverse' ? -1 : 1
    marks.push({ direction: end.direction, x: last.coordinate + sign * 0.08 * spanX, y: end.relative * factor, from: last, title: end.title })
  }
  const allX = [...xs, ...marks.map((m) => m.x)]
  const ys = [...known.map((p) => p.relative! * factor), ...marks.map((m) => m.y), 0]
  const x0 = Math.min(...allX)
  const x1 = Math.max(...allX)
  const pad = (Math.max(...ys) - Math.min(...ys) || 1) * 0.08
  const y0 = Math.min(...ys) - pad
  const y1 = Math.max(...ys) + pad
  const x = (v: number) => left + ((v - x0) / (x1 - x0 || 1)) * (width - left - right)
  const y = (v: number) => top + ((y1 - v) / (y1 - y0 || 1)) * (height - top - bottom)
  const line = known.map((p) => `${x(p.coordinate).toFixed(1)},${y(p.relative! * factor).toFixed(1)}`).join(' ')
  const shown = points[at]
  const tsIndex = points.findIndex((p) => p.side === 'ts')
  const sideEnds = DIRECTIONS.map((d) => {
    const side = points.map((p, i) => ({ p, i })).filter(({ p }) => p.side === d)
    return d === 'reverse' ? side[0] : side[side.length - 1]
  }).filter((e): e is { p: IrcPoint; i: number } => Boolean(e) && e!.p.relative !== null)

  const pick = (event: MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const vx = x0 + (((event.clientX - box.left) / box.width) * width - left) / (width - left - right) * (x1 - x0)
    let best = 0
    points.forEach((p, i) => {
      if (Math.abs(p.coordinate - vx) < Math.abs(points[best].coordinate - vx)) best = i
    })
    onPick(best)
  }

  return (
    <svg
      className="irc-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="IRC energy along the path"
      onClick={pick}
    >
      {ticks(y0, y1).map((v) => (
        <g key={`y${v}`}>
          <line className="grid" x1={left} x2={width - right} y1={y(v)} y2={y(v)} />
          <text className="tick" x={left - 6} y={y(v) + 4} textAnchor="end">
            {v.toFixed(Math.abs(v) < 10 && v !== 0 && !Number.isInteger(v) ? 1 : 0)}
          </text>
        </g>
      ))}
      {ticks(x0, x1, 8).map((v) => (
        <text key={`x${v}`} className="tick" x={x(v)} y={height - bottom + 16} textAnchor="middle">
          {Number.isInteger(v) ? v : v.toFixed(1)}
        </text>
      ))}
      <line className="axis" x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} />
      <line className="axis" x1={left} x2={left} y1={top} y2={height - bottom} />
      <text className="axis-title" x={(left + width - right) / 2} y={height - 6} textAnchor="middle">
        Distance along the path / amu½·bohr ({SIDE_NAMES.reverse.toLowerCase()} ← TS → {SIDE_NAMES.forward.toLowerCase()})
      </text>
      <text className="axis-title" transform={`translate(14 ${(top + height - bottom) / 2}) rotate(-90)`} textAnchor="middle">
        ΔE / {unit}
      </text>
      <polyline className="irc-line" points={line} />
      {marks.map((m) => (
        <g key={m.direction} className="irc-end-point">
          <line
            className="irc-dash"
            x1={x(m.from.coordinate)}
            y1={y(m.from.relative! * factor)}
            x2={x(m.x)}
            y2={y(m.y)}
          />
          <rect x={x(m.x) - 5} y={y(m.y) - 5} width={10} height={10}>
            <title>{m.title}</title>
          </rect>
          <text className="tick" x={x(m.x)} y={y(m.y) - 9} textAnchor="middle">
            opt
          </text>
        </g>
      ))}
      {sideEnds.map(({ p, i }) => (
        <circle key={`end${i}`} className="irc-end" cx={x(p.coordinate)} cy={y(p.relative! * factor)} r={5}>
          <title>
            {pointName(p)}, the last point: ΔE {formatDelta(p.relative, settings)} {unit}
          </title>
        </circle>
      ))}
      {tsIndex >= 0 && points[tsIndex].relative !== null && (
        <g className="irc-ts">
          <circle cx={x(0)} cy={y(0)} r={6}>
            <title>TS</title>
          </circle>
          <text className="tick" x={x(0)} y={y(0) - 10} textAnchor="middle">
            TS
          </text>
        </g>
      )}
      {shown && shown.relative !== null && (
        <circle className="irc-shown" cx={x(shown.coordinate)} cy={y(shown.relative * factor)} r={4} />
      )}
    </svg>
  )
}

type Source = { kind: 'path' } | { kind: 'file'; calculationId: string; file: string }

export function IrcWindow({
  node,
  nodes,
  settings,
  linked,
  onClose,
  onChanged,
}: {
  node: Node
  nodes: Node[]
  settings: Settings | null
  /** The investigation syncs through Git (the import dialog warns about large files). */
  linked: boolean
  onClose: () => void
  /** Something was imported or removed: the history and the node panel's line change. */
  onChanged: (notice: string) => void
}) {
  const [irc, setIrc] = useState<Irc | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [importing, setImporting] = useState<{ part: 'path' | 'end'; direction: IrcDirection | null } | null>(null)
  const [removing, setRemoving] = useState<string | null>(null)
  const [source, setSource] = useState<Source>({ kind: 'path' })
  // Every structure of one path file (the optimization steps between points included), by
  // the calculation they were read for.
  const [read, setRead] = useState<{ id: string; frames: { rows: GeometryRows; energy: number | null }[] } | null>(
    null,
  )
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(5)
  const [copied, setCopied] = useState<string | null>(null)

  const load = useCallback(() => {
    api.irc(node.id).then(
      (found) => {
        setIrc(found)
        setError(null)
        setIndex((i) => Math.min(i, Math.max(found.points.length - 1, 0)))
      },
      (err: unknown) => setError(errorText(err)),
    )
  }, [node.id])
  useEffect(load, [load])

  useEffect(() => {
    if (source.kind !== 'file') return
    let cancelled = false
    const id = source.calculationId
    api.steps(id).then(
      (steps) => {
        if (cancelled) return
        setRead({ id, frames: steps.frames.map((f) => ({ rows: f.geometry, energy: f.energy })) })
        setIndex(0)
      },
      (err: unknown) => !cancelled && setError(errorText(err)),
    )
    return () => {
      cancelled = true
    }
  }, [source])

  const points = useMemo(() => irc?.points ?? [], [irc])
  const fileFrames = source.kind === 'file' && read?.id === source.calculationId ? read.frames : null
  const frames = useMemo(
    () => (source.kind === 'file' ? (fileFrames ?? []).map((f) => f.rows) : points.map((p) => p.rows)),
    [source, fileFrames, points],
  )
  const count = frames.length
  const at = Math.min(index, Math.max(count - 1, 0))
  const xyzFrames = useMemo(() => frames.map((rows, n) => geometryToXyz(rows, `structure ${n + 1}`)), [frames])

  useEffect(() => {
    if (!playing || count < 2) return
    const timer = setInterval(() => setIndex((i) => (Math.min(i, count - 1) + 1) % count), 1000 / speed)
    return () => clearInterval(timer)
  }, [playing, speed, count])

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(null), 2500)
    return () => clearTimeout(timer)
  }, [copied])

  const unit = settings?.energy_unit ?? 'kcal/mol'
  const delta = (value: number | null | undefined) => (value === null || value === undefined ? 'n/a' : formatDelta(value, settings))
  const side = (d: IrcDirection): IrcSide | undefined => irc?.sides.find((s) => s.direction === d)
  const partsOf = (d: IrcDirection, part: 'path' | 'end') =>
    (irc?.parts ?? []).filter((p) => p.part === part && (p.direction === d || p.direction === 'both'))

  const remove = (part: IrcPart) => {
    if (!part.source_file_id) return
    api.removeIrcFile(node.id, part.source_file_id).then(
      () => {
        setRemoving(null)
        if (source.kind === 'file' && part.calculation_ids.includes(source.calculationId)) setSource({ kind: 'path' })
        load()
        onChanged(`Removed ${part.file} from the IRC; the history keeps it.`)
      },
      (err: unknown) => {
        setRemoving(null)
        setError(errorText(err))
      },
    )
  }

  const copyXyz = async () => {
    const rows = frames[at]
    if (!rows) return
    const comment =
      source.kind === 'file'
        ? `${source.file}, structure ${at + 1} of ${count}`
        : copiedComment(irc?.label || node.label, points[at])
    try {
      await navigator.clipboard.writeText(geometryToXyz(rows, comment))
      setCopied(source.kind === 'file' ? `Copied structure ${at + 1}` : `Copied ${pointName(points[at])}`)
    } catch {
      setCopied('The browser did not allow copying')
    }
  }

  if (importing) {
    return (
      <ImportDialog
        file={null}
        irc={importing}
        target={node}
        nodes={nodes}
        energyUnit={unit}
        energyFactor={settings?.energy_factors[unit] ?? 627.5094740631}
        queued={0}
        linked={linked}
        onClose={() => setImporting(null)}
        onImported={(_imported, notice) => {
          setImporting(null)
          load()
          onChanged(notice)
        }}
      />
    )
  }

  const ends = DIRECTIONS.flatMap((d) => {
    const end = side(d)?.end_point
    const relative = end?.values.E.relative
    return end && relative !== null && relative !== undefined
      ? [{ direction: d, relative, title: `${SIDE_NAMES[d]} end point, optimized: ΔE ${delta(relative)} ${unit} (${end.level})` }]
      : []
  })
  const current = source.kind === 'path' ? points[at] : null
  const fileEnergy = source.kind === 'file' ? fileFrames?.[at]?.energy : null
  const pathParts = (irc?.parts ?? []).filter((p) => p.part === 'path')

  const partRow = (part: IrcPart, d: IrcDirection) => {
    const key = part.source_file_id ?? part.file
    const pathCalculation =
      part.calculation_ids.find((id) => points.some((p) => p.calculation_id === id)) ?? part.calculation_ids[0]
    return (
      <li key={key} className={part.used ? undefined : 'muted'}>
        <div>
          <strong className="mono">{part.file}</strong>
          {part.direction === 'both' && <span className="badge">both ways</span>}
        </div>
        <div className="small">
          {part.program} · {part.level}
          {part.part === 'path' && (
            <>
              {' · '}
              {part.points[d] ?? 0} points, {endingText(part.endings[d], part.max_points)}
            </>
          )}
          {part.part === 'end' && <> · {part.types.join(', ')}</>}
          {part.termination !== 'normal' && <span className="badge warn">abnormal termination</span>}
        </div>
        {!part.used && <div className="small">Not used: {part.note}</div>}
        <div className="row">
          {part.part === 'path' && (
            <button
              className="small"
              title="Play every structure this file printed, the optimization steps between the points included"
              onClick={() => {
                setPlaying(false)
                setSource({ kind: 'file', calculationId: pathCalculation, file: part.file })
              }}
            >
              Every structure
            </button>
          )}
          {removing === key ? (
            <>
              <span className="small">Remove this file from the IRC?</span>
              <button className="small danger" onClick={() => remove(part)}>
                Remove
              </button>
              <button className="small" onClick={() => setRemoving(null)}>
                Keep
              </button>
            </>
          ) : (
            <button
              className="small"
              disabled={!part.source_file_id}
              title="Undo this file's import; the history keeps it"
              onClick={() => setRemoving(key)}
            >
              Remove…
            </button>
          )}
        </div>
      </li>
    )
  }

  return (
    <Modal
      title={`IRC of “${node.label || 'Untitled node'}”`}
      onClose={onClose}
      wide
      actions={
        <button className="primary" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="irc" aria-label="IRC files and path">
        {error && <p role="alert">{error}</p>}
        {!irc && !error && <p className="muted">Reading the IRC files…</p>}
        {irc && !irc.is_ts && (
          <p className="notice warn">
            This node's role is not transition state; new IRC files go only on a transition state.
          </p>
        )}
        {irc?.warnings.map((w) => (
          <p key={w} className="warning small">
            {w}
          </p>
        ))}
        {irc && (
          <>
            <div className="irc-slots">
              {DIRECTIONS.map((d) => {
                const s = side(d)
                const end = s?.end_point
                return (
                  <section key={d} className="irc-slot" aria-label={`${SIDE_NAMES[d]} direction`}>
                    <h3>
                      {SIDE_NAMES[d]}
                      {s && s.points > 0 && (
                        <span className="muted small">
                          {' '}
                          {s.points} points, {endingText(s.ending, s.max_points)}
                        </span>
                      )}
                    </h3>
                    <h4>IRC path</h4>
                    <ul className="irc-files">{partsOf(d, 'path').map((p) => partRow(p, d))}</ul>
                    <button
                      className="small"
                      disabled={!irc.is_ts}
                      onClick={() => setImporting({ part: 'path', direction: null })}
                      title="An IRC output; a job running both ways fills both directions, a restarted one continues its direction"
                    >
                      Import IRC…
                    </button>
                    <h4>End point</h4>
                    <ul className="irc-files">{partsOf(d, 'end').map((p) => partRow(p, d))}</ul>
                    {end && (
                      <p className="small">
                        {end.optimization_id
                          ? end.converged === false
                            ? 'The optimization did not converge'
                            : 'Optimized'
                          : 'No optimization yet'}
                        {end.start_rmsd !== null && <>, starting {end.start_rmsd.toFixed(3)} Å from the last IRC point</>}
                        {end.frequency_id
                          ? end.imaginary
                            ? `; ${end.imaginary} imaginary frequenc${end.imaginary === 1 ? 'y' : 'ies'}: not a minimum`
                            : '; no imaginary frequency'
                          : '; no frequency job yet'}
                        .
                      </p>
                    )}
                    <button
                      className="small"
                      disabled={!irc.is_ts}
                      onClick={() => setImporting({ part: 'end', direction: d })}
                      title="The optimization, frequency job or opt freq job run from this end of the IRC"
                    >
                      Import end-point job…
                    </button>
                  </section>
                )
              })}
            </div>

            {pathParts.length === 0 && (
              <p className="muted">
                Import the IRC outputs to see the path. Forward and reverse are the program's names: forward follows the
                imaginary mode as it printed it, so either may lead to the reactant.
              </p>
            )}

            {points.length > 0 && (
              <IrcChart
                points={points}
                ends={ends}
                at={source.kind === 'path' ? at : -1}
                settings={settings}
                onPick={(i) => {
                  setPlaying(false)
                  setSource({ kind: 'path' })
                  setIndex(i)
                }}
              />
            )}

            {count > 0 && (
              <div className="inspect-view">
                <Viewer3D models={[{ xyz: xyzFrames[count - 1] }]} trajectory={{ frames: xyzFrames, index: at }} />
                <div className="inspect-side step-movie">
                  <select
                    aria-label="Structures shown"
                    value={source.kind === 'path' ? '' : source.calculationId}
                    onChange={(event) => {
                      setPlaying(false)
                      const chosen = pathParts.find((p) => p.calculation_ids.includes(event.target.value))
                      setSource(
                        event.target.value && chosen
                          ? { kind: 'file', calculationId: event.target.value, file: chosen.file }
                          : { kind: 'path' },
                      )
                      setIndex(0)
                    }}
                  >
                    <option value="">The IRC path ({points.length} points)</option>
                    {pathParts.map((p) => {
                      const id = p.calculation_ids.find((c) => points.some((q) => q.calculation_id === c)) ?? p.calculation_ids[0]
                      return (
                        <option key={id} value={id}>
                          Every structure of {p.file}
                        </option>
                      )
                    })}
                  </select>
                  {count > 1 && (
                    <div className="step-controls">
                      <button className="small" aria-label="Previous structure" onClick={() => setIndex((at - 1 + count) % count)}>
                        ◀
                      </button>
                      <button className="small" aria-label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying(!playing)}>
                        {playing ? '❚❚' : '▶'}
                      </button>
                      <button className="small" aria-label="Next structure" onClick={() => setIndex((at + 1) % count)}>
                        ▶|
                      </button>
                      <input
                        type="range"
                        aria-label="Point"
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
                    <button className="small" onClick={() => void copyXyz()} title="Copy the coordinates of the structure shown, as xyz text">
                      Copy XYZ
                    </button>
                    {copied && (
                      <span className="muted small" role="status">
                        {copied}
                      </span>
                    )}
                  </div>
                  <p className="step-info small" aria-label="Point shown">
                    {current ? (
                      <>
                        {pointName(current)} · s = {current.coordinate.toFixed(2)} amu½·bohr
                        {current.program_coordinate !== null && current.side !== 'ts' && (
                          <span className="muted"> ({current.program_coordinate.toFixed(2)} as the program printed it)</span>
                        )}
                        {' · '}
                        <span className="mono">
                          ΔE = {delta(current.relative)} {unit}
                        </span>
                        <span className="muted"> · {current.file}</span>
                      </>
                    ) : (
                      <>
                        Structure {at + 1} of {count}
                        {fileEnergy !== null && fileEnergy !== undefined && <span className="mono"> · E = {fileEnergy} Eh</span>}
                      </>
                    )}
                  </p>
                </div>
              </div>
            )}

            {(points.length > 0 || ends.length > 0 || irc.sides.some((s) => s.end_point)) && (
              <section aria-label="IRC energies">
                <h3>Energies relative to the TS</h3>
                <table className="small irc-energies">
                  <thead>
                    <tr>
                      <th />
                      <th>ΔE / {unit}</th>
                      <th>ΔG / {unit}</th>
                      <th>ΔG_qh / {unit}</th>
                      <th>Level</th>
                    </tr>
                  </thead>
                  <tbody>
                    {points.length > 0 && (
                      <tr>
                        <td>TS</td>
                        <td className="mono">{delta(0)}</td>
                        <td />
                        <td />
                        <td>{pathParts[0]?.level}</td>
                      </tr>
                    )}
                    {DIRECTIONS.map((d) => {
                      const s = side(d)
                      if (!s) return null
                      return [
                        s.points > 0 && (
                          <tr key={`${d}-irc`}>
                            <td>{SIDE_NAMES[d]} IRC end</td>
                            <td className="mono">{delta(s.last_relative)}</td>
                            <td className="muted">–</td>
                            <td className="muted">–</td>
                            <td>{partsOf(d, 'path').find((p) => p.used)?.level}</td>
                          </tr>
                        ),
                        s.end_point && (
                          <tr key={`${d}-end`}>
                            <td>{SIDE_NAMES[d]} end point, optimized</td>
                            {TYPES.map((t) => (
                              <td key={t} className="mono" title={s.end_point!.values[t].message ?? undefined}>
                                {delta(s.end_point!.values[t].relative)}
                              </td>
                            ))}
                            <td>{s.end_point.level}</td>
                          </tr>
                        ),
                      ]
                    })}
                  </tbody>
                </table>
                <p className="muted small">
                  The IRC points are relative to the IRC's own TS point; the optimized end points to this TS node at their
                  level ({settings?.standard_state ?? '1 atm'}). None of these feed profiles, the energy table or other
                  analyses. Hover over n/a for the reason.
                </p>
              </section>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
