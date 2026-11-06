import { useContext } from 'react'
import { formatDelta, type Settings, type StepGrid } from '../api'
import { STERIC_PALETTES, StericColourSetting, ramp } from '../display'

const CELL_MAX = 40 // px, the largest cell
const WIDTH = 340 // px of cells across at most
const LEFT = 56 // room for the second coordinate's values
const BOTTOM = 34 // room for the first coordinate's values

/** D219: a 2D grid scan's energies as a map, the first coordinate across and the second up,
 * each cell's colour its converged point's energy above the lowest (the map colours setting,
 * D84). A point that did not converge is crossed, one with no energy left blank. Clicking a cell
 * shows its point; the point shown is outlined. */
export function SurfaceMap({
  grid,
  energies,
  converged,
  pending,
  current,
  settings,
  onPick,
}: {
  grid: StepGrid
  /** per scan point (from 1, index point - 1): its converged structure's energy, or null */
  energies: (number | null)[]
  converged: boolean[]
  /** D220: per scan point, not run yet (a grid still running): left empty, not crossed */
  pending?: boolean[]
  current: number | null
  settings: Settings | null
  onPick: (point: number) => void
}) {
  const { colours } = useContext(StericColourSetting)
  const [across, up] = grid.coordinates
  const n = across.values.length
  const m = up.values.length
  const cell = Math.max(8, Math.min(CELL_MAX, Math.floor(WIDTH / Math.max(n, m))))
  const known = energies.filter((e): e is number => e !== null)
  const low = known.length ? Math.min(...known) : 0
  const high = known.length ? Math.max(...known) : 0
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const stops = STERIC_PALETTES[colours].stops
  const width = LEFT + n * cell + 8
  const height = m * cell + BOTTOM
  const digits = (unitText: string) => (unitText === 'Å' ? 2 : 0)
  const label = (values: number[], k: number, unitText: string) => values[k].toFixed(digits(unitText))
  const every = (count: number) => Math.max(1, Math.ceil(count / 6)) // at most about six labels

  return (
    <figure className="surface-map" aria-label="Energy surface">
      <svg width={width} height={height} role="img" aria-label={`${across.name} by ${up.name}`}>
        {grid.cells.map(([i, j], k) => {
          const point = k + 1
          const energy = energies[k] ?? null
          const x = LEFT + i * cell
          const y = (m - 1 - j) * cell
          const t = energy === null || high === low ? 0 : (energy - low) / (high - low)
          const [r, g, b] = ramp(stops, t)
          const title =
            `Point ${point}: ${across.name} ${label(across.values, i, across.unit)} ${across.unit}, ` +
            `${up.name} ${label(up.values, j, up.unit)} ${up.unit}` +
            (pending?.[k]
              ? ', not run yet'
              : (energy === null ? ', no energy' : `, ${formatDelta(energy - low, settings)} ${unit} above the lowest`) +
                (converged[k] ? '' : ', not converged'))
          return (
            <g key={point} className="surface-cell" onClick={() => onPick(point)} aria-label={title} role="button">
              <title>{title}</title>
              <rect
                x={x}
                y={y}
                width={cell}
                height={cell}
                className={energy === null ? 'surface-empty' : undefined}
                style={energy === null ? undefined : { fill: `rgb(${r} ${g} ${b})` }}
              />
              {!converged[k] && !pending?.[k] && (
                <text x={x + cell / 2} y={y + cell / 2} className="surface-unconverged">
                  ×
                </text>
              )}
            </g>
          )
        })}
        {current !== null && grid.cells[current - 1] && (
          <rect
            className="surface-current"
            x={LEFT + grid.cells[current - 1][0] * cell}
            y={(m - 1 - grid.cells[current - 1][1]) * cell}
            width={cell}
            height={cell}
          />
        )}
        {across.values.map((_, i) =>
          i % every(n) === 0 || i === n - 1 ? (
            <text key={`x${i}`} className="surface-tick" x={LEFT + (i + 0.5) * cell} y={m * cell + 12}>
              {label(across.values, i, across.unit)}
            </text>
          ) : null,
        )}
        {up.values.map((_, j) =>
          j % every(m) === 0 || j === m - 1 ? (
            <text key={`y${j}`} className="surface-tick surface-tick-y" x={LEFT - 4} y={(m - 1 - j + 0.5) * cell}>
              {label(up.values, j, up.unit)}
            </text>
          ) : null,
        )}
        <text className="surface-axis" x={LEFT + (n * cell) / 2} y={m * cell + 28}>
          {across.name} ({across.unit})
        </text>
      </svg>
      <figcaption className="muted small">
        Up: {up.name} ({up.unit}). Colour: energy above the lowest point, 0 to {formatDelta(high - low, settings)} {unit}
        . Click a cell to show that point.
      </figcaption>
    </figure>
  )
}
