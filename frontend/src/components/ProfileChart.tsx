import type { CSSProperties, RefObject } from 'react'
import { balanceText, energyTypeName, formatDelta, type ProfileStyle, type Profiles, type Settings } from '../api'
import { closesCycle } from '../util'
import { dashOf, FONTS, pathwayColours, styleOf } from '../profileStyle'

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

/** The fewest decimals that write every tick exactly. */
function tickDecimals(values: number[]): number {
  for (let d = 0; d < 6; d++) if (values.every((v) => Number(v.toFixed(d)) === v)) return d
  return 6
}

const MARK = { fontWeight: 700, fill: '#b42318' } as const

let measure: CanvasRenderingContext2D | null | undefined

/** The width of `text` in the figure's font, measured by the browser; estimated where it
 * cannot measure. */
function textWidth(text: string, size: number, family: string, weight = 400): number {
  if (measure === undefined) {
    try {
      measure = document.createElement('canvas').getContext('2d')
    } catch {
      measure = null
    }
  }
  if (!measure) return text.length * size * 0.6
  measure.font = `${weight} ${size}px ${family}`
  return measure.measureText(text).width
}

/** FR-EN-05, FR-EN-09: X(n) − X(ref) along each pathway, overlaid in branch colours. A direct
 * connection is a dotted connector, never a barrier (D53, INV-8), tagged "no TS" unless the style
 * hides the tags (D108). Drawn in the
 * profile style of the app's settings (D106), or in `style` when given (the style dialog's
 * preview). `size` sets the size on screen (zoom); the figure itself is always the style's. */
export function ProfileChart({
  data,
  colours: branchColours,
  names,
  settings,
  svgRef,
  marks,
  style: given,
  size,
}: {
  data: Profiles
  colours: string[]
  names: string[]
  settings: Settings | null
  svgRef?: RefObject<SVGSVGElement | null>
  /** Short tags after a point's name, keyed `profile:point`, such as the TDTS and TDI (D86). */
  marks?: Map<string, string>
  style?: ProfileStyle
  size?: { width: number; height: number }
}) {
  const style = given ?? styleOf(settings)
  const colours = pathwayColours(branchColours, style)
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const factor = settings?.energy_factors[unit] ?? 1
  const W = style.width
  const H = style.height
  const f = style.font_size
  const s = f / 11 // every distance that goes with the text scales with it
  const nameSize = (f * 10) / 11
  const row = f + 3
  const ink = style.text === 'black' ? '#000000' : '#1c2430'
  const soft = style.text === 'black' ? '#000000' : '#667085'

  const family = FONTS[style.font]?.family ?? FONTS.system.family
  const typeName = energyTypeName(data.type, data.temperature, data.cutoff, data.standard_state)
  const reference = data.profiles.flatMap((p) => p.points).find((p) => p.id === data.reference_id)
  const title = `Δ${typeName} at ${data.level_label}`
  const subtitle = `relative to ${reference?.label ?? '—'}${data.reference_value === null ? ' (no value at this level)' : ''}`

  const left = style.y_axis ? 70 * s : 24 * s
  const right = 24 * s
  const legendRows = style.legend === 'hidden' ? 0 : names.length
  const legendWidth = 26 * s + Math.max(0, ...names.map((n) => textWidth(n, f, family)))
  const legendLeft = style.legend === 'top-left' ? left : Math.max(left, W - right - legendWidth)
  // D108: the legend starts beside the title only where the title ends before it; otherwise,
  // and always at the top left, it goes below the title, so the two never overlap.
  const titleEnd = left + Math.max(textWidth(title, f + 2, family, 600), textWidth(subtitle, f, family)) + 12 * s
  const legendTop = style.title && (style.legend === 'top-left' || titleEnd > legendLeft) ? 44 * s : 12 * s
  const margin = {
    left,
    right,
    top: Math.max(style.title ? 48 * s : 20 * s, legendRows ? legendTop + legendRows * row + 8 * s : 0),
    bottom: style.step_names ? 56 * s : 28 * s,
  }
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
  const half = Math.min(style.level_width / 2, slotW * 0.45)
  const x = (slot: number) => margin.left + slotW * (slot + 0.5)

  // Labels above or below each level, nearest first: the value, then the name.
  const above = [style.value_position === 'above' && 'value', style.name_position === 'above' && 'name'].filter(
    Boolean,
  ) as ('value' | 'name')[]
  const below = [style.value_position === 'below' && 'value', style.name_position === 'below' && 'name'].filter(
    Boolean,
  ) as ('value' | 'name')[]
  // Room for a second line of labels on the same side, beyond the usual margin.
  const extraTop = Math.max(0, above.length - 1) * row
  const extraBottom = Math.max(0, below.length - 1) * row

  const values = data.profiles.flatMap((p) => p.points.map((pt) => pt.relative).filter((v): v is number => v !== null))
  const shown = [0, ...values.map((v) => v * factor)]
  let lo = Math.min(...shown)
  let hi = Math.max(...shown)
  const pad = (hi - lo || 1) * 0.12
  lo -= pad
  hi += pad
  const innerTop = margin.top + extraTop
  const innerH = plotH - extraTop - extraBottom
  const y = (v: number) => innerTop + innerH - ((v * factor - lo) / (hi - lo)) * innerH
  const yTicks = ticks(lo, hi)
  const unitDecimals = settings?.energy_decimals[unit] ?? 2
  const decimals = style.decimals === 'unit' ? unitDecimals : Number(style.decimals)
  const tickDigits = Math.max(tickDecimals(yTicks), style.decimals === 'unit' ? Math.max(0, unitDecimals - 1) : 0)
  const valueSettings = settings && {
    ...settings,
    energy_decimals: { ...settings.energy_decimals, [unit]: decimals },
  }
  const valueText = (hartree: number) => {
    const text = formatDelta(hartree, valueSettings)
    return style.brackets === 'round' ? `(${text})` : style.brackets === 'square' ? `[${text}]` : text
  }
  // Label placement. A node shared by overlaid pathways is labelled once. Where bars in one
  // column lie close together, their labels go beside them, stacked so none overlap.
  type Label = {
    x: number
    y: number
    value: string
    name: string
    beside: boolean
  } | null
  const labels = new Map<string, Label>()
  slots.forEach((_, slot) => {
    const here: {
      key: string
      id: string
      y: number
      value: string
      name: string
    }[] = []
    data.profiles.forEach((profile, pi) =>
      profile.points.forEach((point, i) => {
        if (point.relative === null || slotOf(pi, i) !== slot) return
        const key = `${pi}:${i}`
        if (here.some((h) => h.id === point.id)) {
          labels.set(key, null) // already labelled for an earlier pathway
          return
        }
        const closing = i === profile.points.length - 1 && closesCycle(profile.points.map((p) => p.id))
        here.push({
          key,
          id: point.id,
          y: y(point.relative),
          value: valueText(point.relative),
          name: `${point.label}${point.is_ts ? ' ‡' : ''}${closing ? ' ↻' : ''}`,
        })
      }),
    )
    here.sort((a, b) => a.y - b.y)
    const crowded = here.some((h, n) => n > 0 && h.y - here[n - 1].y < 18 * s)
    let last = -Infinity
    for (const h of here) {
      const cx = x(slot)
      if (!crowded) {
        labels.set(h.key, {
          x: cx,
          y: h.y,
          value: h.value,
          name: h.name,
          beside: false,
        })
      } else {
        last = Math.max(h.y + 4 * s, last + 12 * s)
        labels.set(h.key, {
          x: cx + half + 4 * s,
          y: last,
          value: h.value,
          name: h.name,
          beside: true,
        })
      }
    }
  })

  const gap = Math.max(6 * s, style.level_thickness / 2 + 3 * s)
  const sizeOf = (line: 'value' | 'name') => (line === 'value' ? f : nameSize)
  const aboveAt = (k: number) => -gap - k * row
  const belowAt = (k: number, line: 'value' | 'name') => gap + 0.85 * sizeOf(line) + k * row
  const connector = (x1: number, y1: number, x2: number, y2: number) => {
    if (style.connector === 'straight') return `M ${x1} ${y1} L ${x2} ${y2}`
    const dx = (x2 - x1) / 2
    return `M ${x1} ${y1} C ${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`
  }
  const svgStyle: CSSProperties | undefined = size && {
    width: size.width,
    height: size.height,
  }

  return (
    <svg
      ref={svgRef}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      role="img"
      aria-label="Energy profile"
      fontFamily={family}
      className="profile-chart"
      style={svgStyle}
    >
      {style.background === 'white' && <rect x={0} y={0} width={W} height={H} fill="#ffffff" />}
      {style.title && (
        <>
          <text x={margin.left} y={18 * s} fontSize={f + 2} fontWeight={600} fill={ink}>
            {title}
          </text>
          <text x={margin.left} y={34 * s} fontSize={f} fill={soft}>
            {subtitle}
          </text>
        </>
      )}
      {legendRows > 0 && (
        <g aria-label="Legend">
          {names.map((name, i) => (
            <g key={i} transform={`translate(${legendLeft}, ${legendTop + i * row})`}>
              <line
                x1={0}
                x2={20 * s}
                y1={0.45 * f}
                y2={0.45 * f}
                stroke={colours[i]}
                strokeWidth={Math.max(2.5, style.connector_width * 1.5)}
                strokeDasharray={dashOf(style, i)}
              />
              <text x={26 * s} y={0.82 * f} fontSize={f} fill={ink}>
                {name}
              </text>
            </g>
          ))}
        </g>
      )}
      {yTicks.map((t) => (
        <g key={t}>
          {style.grid && (
            <line
              x1={margin.left}
              x2={W - margin.right}
              y1={y(t / factor)}
              y2={y(t / factor)}
              stroke={t === 0 ? '#98a2b3' : '#eaecf0'}
            />
          )}
          {style.y_axis && (
            <text x={margin.left - 6 * s} y={y(t / factor) + 0.36 * f} fontSize={f} textAnchor="end" fill={soft}>
              {t.toFixed(tickDigits)}
            </text>
          )}
        </g>
      ))}
      {style.y_axis && (
        <text
          transform={`translate(${16 * s}, ${margin.top + plotH / 2}) rotate(-90)`}
          fontSize={f + 1}
          textAnchor="middle"
          fill={ink}
        >
          Δ{data.type} ({unit})
        </text>
      )}
      {byStep &&
        style.step_names &&
        slots.map((position, i) => {
          const name = data.profiles.flatMap((p) => p.points).find((pt) => pt.step_position === position)?.step_name
          return (
            <text key={position} x={x(i)} y={H - 12 * s} fontSize={f} textAnchor="middle" fill={soft}>
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
                <path
                  d={connector(x1, y1, x2, y2)}
                  fill="none"
                  stroke={colours[pi]}
                  strokeWidth={segment.direct ? Math.max(2, style.connector_width) : style.connector_width}
                  strokeDasharray={segment.direct ? '2 5' : dashOf(style, pi)}
                  strokeLinecap="round"
                />
                {segment.direct && style.edge_tags && (
                  <g aria-label="no TS">
                    <rect
                      x={(x1 + x2) / 2 - 20 * s}
                      y={(y1 + y2) / 2 - 17 * s}
                      width={40 * s}
                      height={14 * s}
                      rx={7 * s}
                      fill="#fff4e5"
                      stroke="#fdb022"
                    />
                    <text
                      x={(x1 + x2) / 2}
                      y={(y1 + y2) / 2 - 6.5 * s}
                      fontSize={nameSize}
                      textAnchor="middle"
                      fill="#93370d"
                    >
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
                <text key={i} x={cx} y={margin.top + 10 * s} fontSize={nameSize} textAnchor="middle" fill="#98a2b3">
                  <title>{point.message ?? point.species_message ?? 'no value'}</title>
                  {point.label}: n/a
                </text>
              )
            }
            const label = labels.get(`${pi}:${i}`)
            const mark = marks?.get(`${pi}:${i}`)
            const markSpan = mark && <tspan {...MARK}> {mark}</tspan>
            // The mark goes after the name, or after the value when the name is hidden.
            const markOn = style.name_position !== 'hidden' ? 'name' : 'value'
            const lines = (side: ('value' | 'name')[], at: (k: number, line: 'value' | 'name') => number) =>
              label &&
              side.map((line, k) => (
                <text
                  key={line}
                  x={label.x}
                  y={label.y + at(k, line)}
                  fontSize={sizeOf(line)}
                  textAnchor="middle"
                  fill={line === 'value' ? colours[pi] : soft}
                >
                  {line === 'value' ? label.value : label.name}
                  {line === markOn && markSpan}
                </text>
              ))
            const hidden = !above.includes(markOn) && !below.includes(markOn)
            return (
              <g key={i}>
                <line
                  x1={cx - half}
                  x2={cx + half}
                  y1={y(point.relative)}
                  y2={y(point.relative)}
                  stroke={colours[pi]}
                  strokeWidth={style.level_thickness}
                  strokeLinecap="round"
                >
                  {point.species.length > 0 && (
                    <title>
                      {/* D69: the free species added or subtracted to balance this point */}
                      {`${point.label} ${balanceText(point.species)}`}
                    </title>
                  )}
                </line>
                {label && label.beside && (
                  <text x={label.x} y={label.y} fontSize={(f * 10.5) / 11} textAnchor="start">
                    {style.value_position !== 'hidden' && <tspan fill={colours[pi]}>{label.value}</tspan>}
                    {style.name_position !== 'hidden' && <tspan fill={soft}> {label.name}</tspan>}
                    {markSpan}
                  </text>
                )}
                {label && !label.beside && (
                  <>
                    {lines(above, (k) => aboveAt(k))}
                    {lines(below, belowAt)}
                    {hidden && markSpan && (
                      <text x={label.x} y={label.y - gap} fontSize={nameSize} textAnchor="middle">
                        {markSpan}
                      </text>
                    )}
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
