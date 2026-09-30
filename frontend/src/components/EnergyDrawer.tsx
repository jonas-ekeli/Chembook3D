import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import {
  api,
  energyTypeName,
  formatDelta,
  type Canvas,
  type EnergyTable,
  type EnergyType,
  type Profiles,
  type ProfileRequest,
  type Settings,
} from '../api'

const NO_BRANCH = '#98a2b3'
const FALLBACK = ['#2459c6', '#c4320a', '#079455', '#6938ef', '#b54708']

type Path = { ids: string[]; choices: { node_id: string; label: string; status: string }[]; branchId: string | null }

// A13: a pathway may end at a node it visited earlier, closing a catalytic cycle once.
function closesCycle(ids: string[]): boolean {
  return ids.slice(0, -1).includes(ids[ids.length - 1])
}

function download(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** Round numbers for the y axis. */
function ticks(min: number, max: number, count = 5): number[] {
  const span = max - min || 1
  const raw = span / count
  const magnitude = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? raw
  const found = []
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) found.push(Number(v.toFixed(10)))
  return found
}

/** FR-EN-05, FR-EN-09: X(n) − X(ref) along each pathway, overlaid in branch colours. A direct
 * connection is a dotted connector labelled "no TS", never a barrier (D53, INV-8). */
function ProfileChart({
  data,
  colours,
  names,
  settings,
  svgRef,
}: {
  data: Profiles
  colours: string[]
  names: string[]
  settings: Settings | null
  svgRef: RefObject<SVGSVGElement | null>
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const factor = settings?.energy_factors[unit] ?? 1
  const W = 960
  const H = 360
  const margin = { left: 70, right: 24, top: 48, bottom: 56 }
  const plotW = W - margin.left - margin.right
  const plotH = H - margin.top - margin.bottom

  // Line the pathways up by reaction step when every one of them moves forward through the
  // steps; otherwise by position along the pathway.
  const byStep = data.profiles.every((p) =>
    p.points.every(
      (pt, i) =>
        pt.step_position !== null && (i === 0 || pt.step_position > (p.points[i - 1].step_position ?? Infinity)),
    ),
  )
  const slots = byStep
    ? [...new Set(data.profiles.flatMap((p) => p.points.map((pt) => pt.step_position as number)))].sort((a, b) => a - b)
    : Array.from({ length: Math.max(...data.profiles.map((p) => p.points.length)) }, (_, i) => i)
  const slotOf = (profileIndex: number, pointIndex: number) =>
    byStep ? slots.indexOf(data.profiles[profileIndex].points[pointIndex].step_position as number) : pointIndex
  const slotW = plotW / Math.max(1, slots.length)
  const half = Math.min(30, slotW * 0.3)
  const x = (slot: number) => margin.left + slotW * (slot + 0.5)

  const values = data.profiles.flatMap((p) => p.points.map((pt) => pt.relative).filter((v): v is number => v !== null))
  const shown = [0, ...values.map((v) => v * factor)]
  let lo = Math.min(...shown)
  let hi = Math.max(...shown)
  const pad = (hi - lo || 1) * 0.12
  lo -= pad
  hi += pad
  const y = (v: number) => margin.top + plotH - ((v * factor - lo) / (hi - lo)) * plotH
  const yTicks = ticks(lo, hi)
  const decimals = settings?.energy_decimals[unit] ?? 2
  const typeName = energyTypeName(data.type, data.temperature, data.cutoff)
  const reference = data.profiles.flatMap((p) => p.points).find((p) => p.id === data.reference_id)
  // Label placement. A node shared by overlaid pathways is labelled once. Where bars in one
  // column lie close together, their labels go beside them, stacked so none overlap.
  type Label = { x: number; y: number; anchor: 'middle' | 'start'; text: string; beside: boolean } | null
  const labels = new Map<string, Label>()
  slots.forEach((_, slot) => {
    const here: { key: string; id: string; y: number; text: string }[] = []
    data.profiles.forEach((profile, pi) =>
      profile.points.forEach((point, i) => {
        if (point.relative === null || slotOf(pi, i) !== slot) return
        const key = `${pi}:${i}`
        if (here.some((h) => h.id === point.id)) {
          labels.set(key, null) // already labelled for an earlier pathway
          return
        }
        const value = formatDelta(point.relative, settings)
        const closing = i === profile.points.length - 1 && closesCycle(profile.points.map((p) => p.id))
        here.push({
          key,
          id: point.id,
          y: y(point.relative),
          text: `${value}|${point.label}${point.is_ts ? ' ‡' : ''}${closing ? ' ↻' : ''}`,
        })
      }),
    )
    here.sort((a, b) => a.y - b.y)
    const crowded = here.some((h, n) => n > 0 && h.y - here[n - 1].y < 18)
    let last = -Infinity
    for (const h of here) {
      const cx = x(slot)
      if (!crowded) {
        labels.set(h.key, { x: cx, y: h.y, anchor: 'middle', text: h.text, beside: false })
      } else {
        last = Math.max(h.y + 4, last + 12)
        labels.set(h.key, { x: cx + half + 4, y: last, anchor: 'start', text: h.text, beside: true })
      }
    }
  })
  return (
    <svg
      ref={svgRef}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      role="img"
      aria-label="Energy profile"
      fontFamily="system-ui, sans-serif"
      className="profile-chart"
    >
      <rect x={0} y={0} width={W} height={H} fill="#ffffff" />
      <text x={margin.left} y={18} fontSize={13} fontWeight={600} fill="#1c2430">
        Δ{typeName} at {data.level_label}
      </text>
      <text x={margin.left} y={34} fontSize={11} fill="#667085">
        relative to {reference?.label ?? '—'}
        {data.reference_value === null ? ' (no value at this level)' : ''}
      </text>
      <g aria-label="Legend">
        {names.map((name, i) => (
          <g key={i} transform={`translate(${W - margin.right - 200}, ${12 + i * 14})`}>
            <rect width={14} height={4} y={3} fill={colours[i]} />
            <text x={20} y={9} fontSize={11} fill="#1c2430">
              {name}
            </text>
          </g>
        ))}
      </g>
      {yTicks.map((t) => (
        <g key={t}>
          <line
            x1={margin.left}
            x2={W - margin.right}
            y1={y(t / factor)}
            y2={y(t / factor)}
            stroke={t === 0 ? '#98a2b3' : '#eaecf0'}
          />
          <text x={margin.left - 6} y={y(t / factor) + 4} fontSize={11} textAnchor="end" fill="#667085">
            {t.toFixed(Math.max(0, decimals - 1))}
          </text>
        </g>
      ))}
      <text
        transform={`translate(16, ${margin.top + plotH / 2}) rotate(-90)`}
        fontSize={12}
        textAnchor="middle"
        fill="#1c2430"
      >
        Δ{data.type} ({unit})
      </text>
      {byStep &&
        slots.map((position, i) => {
          const name = data.profiles.flatMap((p) => p.points).find((pt) => pt.step_position === position)?.step_name
          return (
            <text key={position} x={x(i)} y={H - 12} fontSize={11} textAnchor="middle" fill="#667085">
              {name || `step ${position}`}
            </text>
          )
        })}
      {data.profiles.map((profile, pi) => (
        <g key={pi} aria-label={`Profile ${names[pi]}`}>
          {profile.segments.map((segment, si) => {
            const a = profile.points[si]
            const b = profile.points[si + 1]
            if (a.relative === null || b.relative === null) return null
            const x1 = x(slotOf(pi, si)) + half
            const x2 = x(slotOf(pi, si + 1)) - half
            const y1 = y(a.relative)
            const y2 = y(b.relative)
            return (
              <g key={si} data-direct={segment.direct ? 'true' : undefined}>
                <line
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  stroke={colours[pi]}
                  strokeWidth={segment.direct ? 2 : 1.4}
                  strokeDasharray={segment.direct ? '2 5' : undefined}
                  strokeLinecap="round"
                />
                {segment.direct && (
                  <g aria-label="no TS">
                    <rect
                      x={(x1 + x2) / 2 - 20}
                      y={(y1 + y2) / 2 - 17}
                      width={40}
                      height={14}
                      rx={7}
                      fill="#fff4e5"
                      stroke="#fdb022"
                    />
                    <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 6.5} fontSize={10} textAnchor="middle" fill="#93370d">
                      no TS
                    </text>
                  </g>
                )}
              </g>
            )
          })}
          {profile.points.map((point, i) => {
            const cx = x(slotOf(pi, i))
            if (point.relative === null) {
              return (
                <text key={i} x={cx} y={margin.top + 10} fontSize={10} textAnchor="middle" fill="#98a2b3">
                  <title>{point.message ?? point.species_message ?? 'no value'}</title>
                  {point.label}: n/a
                </text>
              )
            }
            const label = labels.get(`${pi}:${i}`)
            const [value, name] = label ? label.text.split('|') : ['', '']
            return (
              <g key={i}>
                <line
                  x1={cx - half}
                  x2={cx + half}
                  y1={y(point.relative)}
                  y2={y(point.relative)}
                  stroke={colours[pi]}
                  strokeWidth={3.5}
                  strokeLinecap="round"
                >
                  {point.species.length > 0 && (
                    <title>
                      {/* D69: the free species added or subtracted to balance this point */}
                      {`${point.label} ${point.species
                        .map((s) => `${s.count > 0 ? '+' : '−'} ${Math.abs(s.count) > 1 ? `${Math.abs(s.count)} × ` : ''}${s.label}`)
                        .join(' ')}`}
                    </title>
                  )}
                </line>
                {label && label.beside && (
                  <text x={label.x} y={label.y} fontSize={10.5} textAnchor="start">
                    <tspan fill={colours[pi]}>{value}</tspan>
                    <tspan fill="#667085"> {name}</tspan>
                  </text>
                )}
                {label && !label.beside && (
                  <>
                    <text x={label.x} y={label.y - 6} fontSize={11} textAnchor="middle" fill={colours[pi]}>
                      {value}
                    </text>
                    <text x={label.x} y={label.y + 15} fontSize={10} textAnchor="middle" fill="#667085">
                      {name}
                    </text>
                  </>
                )}
              </g>
            )
          })}
        </g>
      ))}
    </svg>
  )
}

/** WF-08 bottom drawer: pathways chosen along edges, the energy profile and the energy table. */
export function EnergyDrawer({
  canvas,
  settings,
  level,
  levelLabel,
  type,
  referenceId,
  selectedId,
  refreshKey,
  onReference,
  onSelectNode,
}: {
  canvas: Canvas
  settings: Settings | null
  level: string | null
  levelLabel: string
  type: EnergyType
  referenceId: string | null
  selectedId: string | null
  refreshKey: number
  onReference: (id: string | null) => void
  onSelectNode: (id: string) => void
}) {
  const [tab, setTab] = useState<'profile' | 'table'>('profile')
  const [paths, setPaths] = useState<Path[]>([])
  const [profiles, setProfiles] = useState<Profiles | null>(null)
  const [table, setTable] = useState<EnergyTable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const labels = useMemo(() => {
    const found = new Map<string, string>()
    for (const n of canvas.nodes) found.set(n.id, n.label || 'Untitled node')
    for (const g of canvas.groups) found.set(g.id, g.label || 'Group')
    return found
  }, [canvas])
  const branches = useMemo(() => new Map(canvas.branches.map((b) => [b.id, b])), [canvas.branches])
  const branchOf = (id: string) =>
    canvas.nodes.find((n) => n.id === id)?.branch_id ?? canvas.groups.find((g) => g.id === id)?.outgoing_branch_id ?? null

  // Records can be deleted elsewhere; drop pathways that lost a node.
  const livePaths = paths.filter((p) => p.ids.every((id) => labels.has(id)))
  const onPaths = new Set(livePaths.flatMap((p) => p.ids))
  const reference = referenceId && onPaths.has(referenceId) ? referenceId : (livePaths[0]?.ids[0] ?? null)
  const colours = livePaths.map((p, i) => {
    const branchId = p.branchId ?? [...p.ids].reverse().map(branchOf).find((b) => b)
    return branchId ? (branches.get(branchId)?.colour ?? NO_BRANCH) : FALLBACK[i % FALLBACK.length]
  })
  const names = livePaths.map((p) => {
    const branch = p.branchId ? branches.get(p.branchId) : undefined
    return branch ? `${branch.name || 'Unnamed branch'}` : `${labels.get(p.ids[0])} → ${labels.get(p.ids.at(-1)!)}`
  })

  const request: ProfileRequest | null =
    level && livePaths.length
      ? { paths: livePaths.map((p) => p.ids), reference_id: reference, level, type, unit: settings?.energy_unit }
      : null
  const requestKey = JSON.stringify(request)

  useEffect(() => {
    const request = JSON.parse(requestKey) as ProfileRequest | null
    if (!request) return
    let current = true
    Promise.all([api.profiles(request), api.energyTable(request)]).then(
      ([p, t]) => {
        if (!current) return
        setProfiles(p)
        setTable(t)
        setError(null)
      },
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [requestKey, refreshKey])

  const replace = (index: number, next: Path | null) =>
    setPaths((current) => (next ? current.map((p, i) => (i === index ? next : p)) : current.filter((_, i) => i !== index)))

  const extend = (index: number, ids: string[], branchId: string | null) =>
    api.extendPathway(ids, branchId).then(
      (found) => replace(index, { ids: found.path, choices: found.choices, branchId }),
      (err: unknown) => setError(errorText(err)),
    )

  const startAtSelection = () => {
    if (!selectedId) return
    api.extendPathway([selectedId]).then(
      (found) => setPaths((current) => [...current, { ids: found.path, choices: found.choices, branchId: null }]),
      (err: unknown) => setError(errorText(err)),
    )
  }

  const addBranch = (branchId: string) =>
    api.branchPathway(branchId).then(
      (found) => setPaths((current) => [...current, { ids: found.path, choices: found.choices, branchId }]),
      (err: unknown) => setError(errorText(err)),
    )

  // Shown only while there is something to show; a stale result is never drawn.
  const shownProfiles = request ? profiles : null
  const shownTable = request ? table : null

  const exportSvg = () => {
    if (!svgRef.current) return
    const text = new XMLSerializer().serializeToString(svgRef.current)
    const url = URL.createObjectURL(new Blob([text], { type: 'image/svg+xml' }))
    download(url, 'energy-profile.svg')
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const exportPng = () => {
    if (!svgRef.current) return
    const text = new XMLSerializer().serializeToString(svgRef.current)
    const image = new Image()
    const scale = 2
    image.onload = () => {
      const canvasEl = document.createElement('canvas')
      canvasEl.width = 960 * scale
      canvasEl.height = 360 * scale
      const context = canvasEl.getContext('2d')!
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, canvasEl.width, canvasEl.height)
      context.drawImage(image, 0, 0, canvasEl.width, canvasEl.height)
      download(canvasEl.toDataURL('image/png'), 'energy-profile.png')
    }
    image.onerror = () => setError('Could not export the profile image')
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`
  }

  const exportCsv = () => {
    if (!request) return
    api.energyTableCsv(request).then(
      (blob) => {
        const url = URL.createObjectURL(blob)
        download(url, 'energy-table.csv')
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  return (
    <section className="drawer" aria-label="Energy drawer">
      <div className="drawer-head">
        <div className="segmented" role="group" aria-label="Drawer tab">
          <button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>
            Energy profile
          </button>
          <button aria-pressed={tab === 'table'} onClick={() => setTab('table')}>
            Energy table
          </button>
        </div>
        <span className="muted small">
          {level ? `${energyTypeName(type, settings?.qh_temperature, settings?.qh_cutoff)} at ${levelLabel}` : 'No energies in this investigation yet'}
        </span>
        <span className="spacer" />
        {tab === 'profile' ? (
          <>
            <button className="small" onClick={exportPng} disabled={!shownProfiles}>
              Save profile as PNG
            </button>
            <button className="small" onClick={exportSvg} disabled={!shownProfiles}>
              Save profile as SVG
            </button>
          </>
        ) : (
          <button className="small" onClick={exportCsv} disabled={!shownTable}>
            Export CSV
          </button>
        )}
      </div>
      <div className="drawer-body">
        <div className="pathways" aria-label="Pathways">
          {livePaths.map((path, index) => (
            <div key={index} className="pathway" role="group" aria-label={`Pathway ${names[index]}`}>
              <div className="pathway-head">
                <span className="swatch" style={{ background: colours[index] }} />
                <strong>{names[index]}</strong>
                <span className="spacer" />
                <button
                  className="small"
                  disabled={path.ids.length < 2}
                  onClick={() => replace(index, { ...path, ids: path.ids.slice(0, -1), choices: [] })}
                  title="Remove the last node"
                >
                  Undo step
                </button>
                <button className="small" onClick={() => replace(index, null)} aria-label={`Remove pathway ${names[index]}`}>
                  ✕
                </button>
              </div>
              <p className="pathway-nodes">
                {path.ids.map((id, i) => (
                  <span key={i}>
                    {i > 0 && ' → '}
                    <button className="link" onClick={() => onSelectNode(id)}>
                      {labels.get(id)}
                    </button>
                    {i === path.ids.length - 1 && closesCycle(path.ids) && (
                      <span className="cycle-mark" title="Cycle closed">
                        {' '}
                        ↻
                      </span>
                    )}
                  </span>
                ))}
              </p>
              {closesCycle(path.ids) && <p className="muted small">Cycle closed.</p>}
              {path.choices.length > 0 && (
                <div className="choices" role="group" aria-label="Continue with">
                  <span className="muted small">Continue with:</span>
                  {path.choices.map((choice) => (
                    <button
                      key={choice.node_id}
                      className="small"
                      onClick={() => extend(index, [...path.ids, choice.node_id], path.branchId)}
                    >
                      {choice.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          <div className="pathway-add">
            <button className="small" onClick={startAtSelection} disabled={!selectedId}>
              Start at selected node
            </button>
            <select
              aria-label="Add branch pathway"
              value=""
              onChange={(event) => event.target.value && addBranch(event.target.value)}
            >
              <option value="">Add a branch…</option>
              {canvas.branches.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name || 'Unnamed branch'}
                </option>
              ))}
            </select>
          </div>
          {livePaths.length > 0 && (
            <label className="field">
              <span>Reference</span>
              <select
                aria-label="Reference node"
                value={reference ?? ''}
                onChange={(event) => onReference(event.target.value || null)}
              >
                {[...onPaths].map((id) => (
                  <option key={id} value={id}>
                    {labels.get(id)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {error && (
            <p role="alert" className="error small">
              {error}
            </p>
          )}
        </div>
        <div className="drawer-main">
          {!livePaths.length ? (
            <p className="muted placeholder">
              Add a branch, or select a node on the canvas and start a pathway there. Pathways follow the transitions
              you drew; at a fork you choose how to go on.
            </p>
          ) : tab === 'profile' ? (
            shownProfiles && <ProfileChart data={shownProfiles} colours={colours} names={names} settings={settings} svgRef={svgRef} />
          ) : (
            shownTable && (
              <table className="energy-table" aria-label="Energy table">
                <thead>
                  <tr>
                    {shownTable.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shownTable.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className={j >= 4 ? 'num' : undefined}>
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </div>
      </div>
    </section>
  )
}
