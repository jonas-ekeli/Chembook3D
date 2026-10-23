import type { EnergyTable, EnergyType } from '../api'

// The columns as `pathways.table` sends them, which the CSV keeps: Label, Step, Branch, Level,
// E, H, G, G_qh, Thermochemistry, [BSSE (D161)], Free species, ΔX, Check.
const LEVEL = 3
const THERMO = 8
const ABSOLUTE = [4, 5, 6, 7]
const BSSE = 9
const BSSE_COLUMN = 'BSSE correction (hartree)'

/** D175, D176: a real minus sign, as on the cards and the arrows. */
const minus = (text: string) => text.replace(/^-/, '−')

/**
 * D177: the energy table with the relative energies first: Label (with a TS tag) · Step ·
 * Branch (its colour) · ΔX · ΔX‡ of step · the absolute values in hartree under one header ·
 * Free species · Check. Level and Thermochemistry move into a caption when every row has the
 * same; otherwise they stay as columns after the absolute values. The row of the node selected
 * on the canvas is highlighted. A read-only copy made before D177 has no `points`, so it shows
 * no TS tags, colours, barriers or highlight.
 */
export function EnergyTableView({
  table,
  type,
  reference,
  colourOf,
  selectedId,
}: {
  table: EnergyTable
  type: EnergyType
  /** The reference's label, for the header over the relative values. */
  reference: string | null
  colourOf: (branchId: string) => string | undefined
  selectedId: string | null
}) {
  const { columns, rows } = table
  const points = table.points?.length === rows.length ? table.points : undefined
  const n = columns.length
  const free = n - 3
  const relative = n - 2
  const check = n - 1
  const absolute = columns[BSSE] === BSSE_COLUMN ? [...ABSOLUTE, BSSE] : ABSOLUTE
  const constant = [LEVEL, THERMO].filter((j) => rows.length > 0 && rows.every((row) => row[j] === rows[0][j]))
  const tail = [...[LEVEL, THERMO].filter((j) => !constant.includes(j)), free, check]
  const caption = [...constant.map((j) => rows[0][j]), columns[relative]].filter(Boolean).join(' · ')
  const unit = /\(([^()]*)\)$/.exec(columns[relative])?.[1] ?? ''
  const symbol = `Δ${type}`

  return (
    <>
      <p className="energy-caption">{caption}</p>
      <table className="energy-table drawer-table" aria-label="Energy table">
        <thead>
          <tr className="group-row">
            <td colSpan={3} />
            <th colSpan={2} scope="colgroup">
              {unit}, relative to {reference ?? 'the reference'}
            </th>
            <th colSpan={absolute.length} scope="colgroup" className="abs-group">
              Absolute (hartree)
            </th>
            <td colSpan={tail.length} />
          </tr>
          <tr>
            <th className="text">{columns[0]}</th>
            <th className="text">{columns[1]}</th>
            <th className="text">{columns[2]}</th>
            <th title={columns[relative]}>{symbol}</th>
            <th
              title={`${symbol}‡ of the step into each transition state: the TS less the lowest state before it on the pathway since the previous TS (where the step starts), not the reference (D177)`}
            >
              {symbol}‡ of step
            </th>
            {absolute.map((j) => (
              <th key={j} title={columns[j]}>
                {columns[j].replace(/ \(hartree\)$/, '')}
              </th>
            ))}
            {tail.map((j) => (
              <th key={j} className="text">
                {columns[j]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const point = points?.[i]
            const colour = point?.branch_id ? colourOf(point.branch_id) : undefined
            return (
              <tr
                key={i}
                className={point && point.id === selectedId ? 'selected' : undefined}
                aria-current={point && point.id === selectedId ? 'true' : undefined}
              >
                <td>
                  {row[0]}
                  {point?.is_ts && (
                    <span className="ts-mark" title="Transition state">
                      TS
                    </span>
                  )}
                </td>
                <td>{row[1]}</td>
                <td>
                  {colour && row[2] && <span className="swatch" style={{ background: colour }} />}
                  {row[2]}
                </td>
                <td className="rel">{minus(row[relative])}</td>
                <td className="num">{minus(point?.barrier ?? '')}</td>
                {absolute.map((j) => (
                  <td key={j} className="abs">
                    {row[j]}
                  </td>
                ))}
                {tail.map((j) => (
                  <td key={j} className={j === check ? 'check' : undefined}>
                    {row[j]}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}
