import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  energyTypeName,
  formatDelta,
  type Canvas,
  type EnergyOptions,
  type EnergyType,
  type Selectivity,
  type SelectivityFields,
  type SelectivityOutcome,
  type SelectivityResult,
  type Settings,
  type Turnover,
  type TurnoverFields,
  type TurnoverResult,
} from '../api'
import { closesCycle, download } from '../util'
import { ProfileChart } from './EnergyDrawer'
import { Notes, TextField } from './Fields'
import { Modal } from './Modal'

// D83: the Analyses view lists the saved selectivities (S7); each one compares outcomes
// realised by TS nodes or groups at one level and energy type (S1–S6).
// D86: it also lists the turnovers, each the TOF of one closed cycle (T1–T5).

const ENERGY_TYPES: EnergyType[] = ['E', 'H', 'G', 'G_qh']
const METHODS = { boltzmann: 'Boltzmann sum', lowest: 'Lowest TS only' } as const

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function percent(value: number | null): string {
  return value === null ? 'n/a' : `${value.toFixed(1)} %`
}

/** A number that saves when it loses focus; empty means null. */
function NumberInput({
  label,
  value,
  placeholder,
  positive = false,
  onCommit,
}: {
  label: string
  value: number | null
  placeholder?: string
  positive?: boolean
  onCommit: (value: number | null) => void
}) {
  const [draft, setDraft] = useState(value === null ? '' : String(value))
  const parsed = Number(draft)
  const valid = draft.trim() === '' || (Number.isFinite(parsed) && (positive ? parsed > 0 : parsed >= 0))
  const commit = () => {
    if (!valid) return
    const next = draft.trim() === '' ? null : parsed
    if (next !== value) onCommit(next)
  }
  return (
    <input
      aria-label={label}
      inputMode="decimal"
      value={draft}
      placeholder={placeholder}
      aria-invalid={!valid}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit()
      }}
    />
  )
}

/** S1: an outcome is one or more TS nodes, or groups whose members all count. */
function OutcomeRow({
  outcome,
  canvas,
  onChange,
  onRemove,
}: {
  outcome: SelectivityOutcome
  canvas: Canvas
  onChange: (outcome: SelectivityOutcome) => void
  onRemove: () => void
}) {
  const nodes = new Map(canvas.nodes.map((n) => [n.id, n]))
  const groups = new Map(canvas.groups.map((g) => [g.id, g]))
  const memberName = (id: string) => {
    const group = groups.get(id)
    if (group) return `${group.label || 'Group'} (group)`
    const node = nodes.get(id)
    return node ? node.label || 'Untitled node' : 'a deleted node'
  }
  const free = (id: string) => !outcome.members.includes(id)
  const ts = canvas.nodes.filter((n) => n.role === 'transition_state' && free(n.id))
  const others = canvas.nodes.filter((n) => n.role !== 'transition_state' && free(n.id))
  const freeGroups = canvas.groups.filter((g) => free(g.id))
  const name = outcome.name
  return (
    <tr>
      <td>
        <input
          key={name}
          aria-label="Outcome name"
          defaultValue={name}
          onBlur={(event) => {
            const next = event.target.value.trim()
            if (next && next !== name) onChange({ ...outcome, name: next })
            else event.target.value = name
          }}
        />
      </td>
      <td>
        <ul className="chips" aria-label={`Transition states of ${name}`}>
          {outcome.members.map((id) => (
            <li key={id} className="tag">
              {memberName(id)}
              <button
                className="tag-remove"
                aria-label={`Remove ${memberName(id)} from ${name}`}
                onClick={() => onChange({ ...outcome, members: outcome.members.filter((m) => m !== id) })}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <select
          aria-label={`Add to ${name}`}
          value=""
          onChange={(event) => onChange({ ...outcome, members: [...outcome.members, event.target.value] })}
        >
          <option value="" disabled>
            Add a TS or group…
          </option>
          {ts.length > 0 && (
            <optgroup label="Transition states">
              {ts.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label || 'Untitled node'}
                </option>
              ))}
            </optgroup>
          )}
          {freeGroups.length > 0 && (
            <optgroup label="Groups (all members)">
              {freeGroups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.label || 'Group'}
                </option>
              ))}
            </optgroup>
          )}
          {others.length > 0 && (
            <optgroup label="Other nodes">
              {others.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label || 'Untitled node'}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </td>
      <td>
        <NumberInput
          key={String(outcome.experimental)}
          label={`Experimental amount of ${name}`}
          value={outcome.experimental}
          placeholder="—"
          onCommit={(experimental) => onChange({ ...outcome, experimental })}
        />
      </td>
      <td>
        <button className="small icon danger" aria-label={`Remove outcome ${name}`} onClick={onRemove}>
          ×
        </button>
      </td>
    </tr>
  )
}

function ResultView({
  result,
  settings,
  onSelectNode,
}: {
  result: SelectivityResult
  settings: Settings | null
  onSelectNode: (id: string) => void
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const lead = result.conformers
  const other = lead === 'boltzmann' ? 'lowest' : 'boltzmann'
  const ok = result.status === 'ok'
  const experiment = result.outcomes.some((o) => o.experimental_percent !== null)
  const ratio = (key: 'boltzmann_percent' | 'lowest_percent' | 'experimental_percent') =>
    result.outcomes.map((o) => (o[key] ?? 0).toFixed(1)).join(' : ')
  const names = result.outcomes.map((o) => o.name).join(' : ')
  const excess = result.excess
  const leadExcess = excess?.[lead]
  return (
    <section aria-label="Selectivity result">
      <h3>Result</h3>
      {result.level_label && (
        <p className="muted small">
          {result.level_label} · {energyTypeName(result.energy_type, result.temperature, result.cutoff)} · Boltzmann
          factors at {result.temperature} K
          {result.temperature_from_settings ? ' (the G_qh temperature in Settings)' : ''}
        </p>
      )}
      {ok ? (
        <p className="headline" aria-label="Predicted ratio">
          <strong>
            {names} = {ratio(`${lead}_percent`)}
          </strong>
          {leadExcess && (
            <>
              {' '}
              · {excess.label} {leadExcess.value.toFixed(1)} % ({leadExcess.major})
            </>
          )}
          <span className="muted"> · {METHODS[lead]}</span>
        </p>
      ) : (
        <p className="notice" role="status">
          {result.status === 'n/a' ? 'n/a: ' : ''}
          {result.message}
        </p>
      )}
      {result.outcomes.length > 0 && (
        <table className="energy-table selectivity-table" aria-label="Outcomes">
          <thead>
            <tr>
              <th>Outcome</th>
              <th>ΔΔG‡ ({unit})</th>
              <th>Predicted</th>
              <th className="muted">
                {METHODS[other]}: ΔΔG‡
              </th>
              <th className="muted">%</th>
              {experiment && (
                <>
                  <th>Experiment</th>
                  <th>Exp. ΔΔG‡ ({unit})</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {result.outcomes.map((o) => (
              <tr key={o.id}>
                <td>{o.name}</td>
                <td className="num">{formatDelta(o[`${lead}_ddg`], settings)}</td>
                <td className="num">{percent(o[`${lead}_percent`])}</td>
                <td className="num muted">{formatDelta(o[`${other}_ddg`], settings)}</td>
                <td className="num muted">{percent(o[`${other}_percent`])}</td>
                {experiment && (
                  <>
                    <td className="num">{percent(o.experimental_percent)}</td>
                    <td className="num">
                      {o.experimental_percent !== null && o.experimental_ddg === null
                        ? '—'
                        : formatDelta(o.experimental_ddg, settings)}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {excess && ok && excess[other] && (
        <p className="muted small">
          {other === 'lowest' ? 'With only the lowest TS' : 'With the Boltzmann sum'}: {excess.label}{' '}
          {excess[other].value.toFixed(1)} % ({excess[other].major})
        </p>
      )}
      {experiment && (
        <p className="small" aria-label="Experiment">
          Experiment: {names} = {ratio('experimental_percent')}
          {excess?.experimental &&
            `, ${excess.label} ${excess.experimental.value.toFixed(1)} % (${excess.experimental.major})`}
        </p>
      )}
      {result.outcomes.some((o) => o.members.length > 0) && (
        <table className="energy-table selectivity-table" aria-label="Transition states">
          <thead>
            <tr>
              <th>Outcome</th>
              <th>Transition state</th>
              <th>ΔG from lowest ({unit})</th>
              <th>Share of outcome</th>
              <th>Share of all</th>
            </tr>
          </thead>
          <tbody>
            {result.outcomes.flatMap((o) =>
              o.members.map((m) => (
                <tr key={`${o.id}-${m.node_id}`}>
                  <td>{o.name}</td>
                  <td>
                    <button className="link" onClick={() => onSelectNode(m.node_id)}>
                      {m.label}
                    </button>
                    {m.group_label && <span className="muted"> in {m.group_label}</span>}
                  </td>
                  <td className="num" title={m.message ?? undefined}>
                    {m.value === null ? (m.message ?? 'n/a') : formatDelta(m.relative, settings)}
                  </td>
                  <td className="num">{percent(m.share_in_outcome)}</td>
                  <td className="num">{percent(m.share)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
      {result.notes.length > 0 && (
        <ul className="warnings" aria-label="Result notes">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function SelectivityEditor({
  selectivity,
  canvas,
  settings,
  energyOptions,
  refreshKey,
  onSaved,
  onDeleted,
  onSelectNode,
}: {
  selectivity: Selectivity
  canvas: Canvas
  settings: Settings | null
  energyOptions: EnergyOptions | null
  refreshKey: number
  onSaved: (selectivity: Selectivity) => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
}) {
  const [result, setResult] = useState<SelectivityResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    let current = true
    api.selectivityResult(selectivity.id).then(
      (found) => current && setResult(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [selectivity, refreshKey])

  const save = (fields: SelectivityFields) =>
    api.updateSelectivity(selectivity.id, fields).then(
      (saved) => {
        setError(null)
        onSaved(saved)
      },
      (err: unknown) => setError(errorText(err)),
    )
  const saveOutcomes = (outcomes: SelectivityOutcome[]) =>
    save({ outcomes: outcomes.map(({ name, members, experimental }) => ({ name, members, experimental })) })

  const level = energyOptions?.levels.find((l) => l.key === selectivity.level)
  const types = level?.types.length ? ENERGY_TYPES.filter((t) => level.types.includes(t) || t === selectivity.energy_type) : ENERGY_TYPES
  const outcomes = selectivity.outcomes
  const freeName = () => {
    for (let n = outcomes.length + 1; ; n++) {
      if (!outcomes.some((o) => o.name === `Outcome ${n}`)) return `Outcome ${n}`
    }
  }

  return (
    <div className="inspector" aria-label="Selectivity">
      <div className="inspector-head">
        <h2>{selectivity.name}</h2>
        <button className="danger" onClick={() => setDeleting(true)}>
          Delete…
        </button>
      </div>
      <section className="fields" aria-label="Selectivity settings">
        <TextField key={selectivity.name} label="Name" value={selectivity.name} onCommit={(name) => save({ name })} />
        <div className="field-pair">
          <label className="field">
            <span>Level of theory</span>
            <select
              aria-label="Selectivity level"
              value={selectivity.level ?? ''}
              onChange={(event) => save({ level: event.target.value || null })}
            >
              {!level && (
                <option value="">{selectivity.level ? 'A level no longer listed' : 'Choose…'}</option>
              )}
              {energyOptions?.levels.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Energy</span>
            <select
              aria-label="Selectivity energy type"
              value={selectivity.energy_type}
              onChange={(event) => save({ energy_type: event.target.value as EnergyType })}
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {energyTypeName(t, result?.temperature, result?.cutoff)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-pair">
          <label className="field">
            <span>Temperature (K)</span>
            <NumberInput
              key={String(selectivity.temperature)}
              label="Selectivity temperature"
              value={selectivity.temperature}
              positive
              placeholder={`${settings?.qh_temperature ?? 298.15} (Settings)`}
              onCommit={(temperature) => save({ temperature })}
            />
          </label>
          <label className="field">
            <span>Conformers of an outcome</span>
            <select
              aria-label="Conformers"
              value={selectivity.conformers}
              onChange={(event) => save({ conformers: event.target.value as Selectivity['conformers'] })}
            >
              <option value="boltzmann">Boltzmann sum over all its TSs (Curtin–Hammett)</option>
              <option value="lowest">Only its lowest TS</option>
            </select>
          </label>
        </div>
        <label className="field">
          <span>For two outcomes, show</span>
          <select
            aria-label="Excess"
            value={selectivity.excess}
            onChange={(event) => save({ excess: event.target.value as Selectivity['excess'] })}
          >
            <option value="ee">ee (enantiomers)</option>
            <option value="de">de (diastereomers)</option>
            <option value="none">the ratio only</option>
          </select>
        </label>
        <p className="muted small">
          Leave the temperature empty to use the G_qh temperature in Settings. G_qh is recomputed at this temperature
          from the stored frequencies; E, H and G are used as read from the files.
        </p>
      </section>
      <section aria-label="Outcomes">
        <div className="section-head">
          <h3>Outcomes</h3>
          <button onClick={() => saveOutcomes([...outcomes, { name: freeName(), members: [], experimental: null }])}>
            Add outcome
          </button>
        </div>
        {outcomes.length === 0 ? (
          <p className="muted">No outcomes yet. Add one for each product, such as R and S.</p>
        ) : (
          <table className="members outcomes">
            <thead>
              <tr>
                <th>Outcome</th>
                <th>Transition states</th>
                <th title="Any scale, such as 95 and 5, or 19 and 1">Experiment</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {outcomes.map((outcome, index) => (
                <OutcomeRow
                  key={index}
                  outcome={outcome}
                  canvas={canvas}
                  onChange={(next) => saveOutcomes(outcomes.map((o, i) => (i === index ? next : o)))}
                  onRemove={() => saveOutcomes(outcomes.filter((_, i) => i !== index))}
                />
              ))}
            </tbody>
          </table>
        )}
        <p className="muted small">
          A group counts with all its members. Experimental amounts are optional, on any scale (95 and 5, or 19 and 1).
        </p>
      </section>
      {error && <p role="alert">{error}</p>}
      {result && <ResultView result={result} settings={settings} onSelectNode={onSelectNode} />}
      <Notes key={selectivity.notes} notes={selectivity.notes} onSave={(notes) => save({ notes })} />
      {deleting && (
        <Modal
          title="Delete selectivity"
          onClose={() => setDeleting(false)}
          actions={
            <>
              <button onClick={() => setDeleting(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() =>
                  api.deleteSelectivity(selectivity.id).then(
                    () => {
                      setDeleting(false)
                      onDeleted()
                    },
                    (err: unknown) => {
                      setDeleting(false)
                      setError(errorText(err))
                    },
                  )
                }
              >
                Delete
              </button>
            </>
          }
        >
          <p>
            Delete “{selectivity.name}”? The transition states and their calculations are kept; the deletion is
            recorded in the history.
          </p>
        </Modal>
      )}
    </div>
  )
}

const SUPERSCRIPT: Record<string, string> = {
  '-': '⁻',
  '0': '⁰',
  '1': '¹',
  '2': '²',
  '3': '³',
  '4': '⁴',
  '5': '⁵',
  '6': '⁶',
  '7': '⁷',
  '8': '⁸',
  '9': '⁹',
}

/** 1234 as "1.23 × 10³"; between 0.01 and 1000 as a plain number. */
function scientific(value: number | null): string {
  if (value === null) return 'n/a'
  if (!Number.isFinite(value)) return value > 0 ? '∞' : '−∞'
  if (value === 0) return '0'
  const size = Math.abs(value)
  if (size >= 0.01 && size < 1000) return value.toPrecision(3)
  const [mantissa, exponent] = value.toExponential(2).split('e')
  const power = String(Number(exponent))
    .split('')
    .map((c) => SUPERSCRIPT[c] ?? c)
    .join('')
  return `${mantissa} × 10${power}`
}

function TurnoverResultView({
  turnover,
  result,
  canvas,
  settings,
  onSelectNode,
}: {
  turnover: Turnover
  result: TurnoverResult
  canvas: Canvas
  settings: Settings | null
  onSelectNode: (id: string) => void
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const svgRef = useRef<SVGSVGElement>(null)
  const ok = result.status === 'ok'
  const point = (index: number | null) => (index === null ? null : result.points[index])
  const tdts = point(result.tdts)
  const tdi = point(result.tdi)
  const first = result.cycle[0]
  const branchId =
    canvas.nodes.find((n) => n.id === first)?.branch_id ?? canvas.groups.find((g) => g.id === first)?.outgoing_branch_id
  const colour = canvas.branches.find((b) => b.id === branchId)?.colour ?? '#2459c6'
  const marks = new Map<string, string>()
  if (result.tdts !== null) marks.set(`0:${result.tdts}`, 'TDTS')
  if (result.tdi !== null) marks.set(`0:${result.tdi}`, 'TDI')
  const comparison = result.comparison
  const typeName = energyTypeName(result.energy_type, result.temperature, result.cutoff)
  return (
    <section aria-label="Turnover result">
      <h3>Result</h3>
      {result.level_label && (
        <p className="muted small">
          {result.level_label} · {typeName} · {result.temperature} K
          {result.temperature_from_settings ? ' (the G_qh temperature in Settings)' : ''}
        </p>
      )}
      {ok ? (
        <>
          <p className="headline" aria-label="TOF">
            <strong>
              TOF = {scientific(result.tof)} s⁻¹
            </strong>{' '}
            <span className="muted">({scientific(result.tof !== null ? result.tof * 3600 : null)} h⁻¹)</span>
          </p>
          <p aria-label="Energetic span">
            δE = {formatDelta(result.span, settings)} {unit} · TDTS{' '}
            <button className="link" onClick={() => tdts && onSelectNode(tdts.id)}>
              {tdts?.label}
            </button>{' '}
            · TDI{' '}
            <button className="link" onClick={() => tdi && onSelectNode(tdi.id)}>
              {tdi?.label}
            </button>{' '}
            · Δ{result.energy_type}
            <sub>r</sub> = {formatDelta(result.reaction, settings)} {unit}
          </p>
          <p className="muted small">
            From δE alone, (k<sub>B</sub>T/h)·e<sup>−δE/RT</sup> = {scientific(result.tof_span)} s⁻¹.
          </p>
        </>
      ) : (
        <p className="notice" role="status">
          {result.status === 'n/a' ? 'n/a: ' : ''}
          {result.message}
        </p>
      )}
      {comparison && (
        <p aria-label="Comparison">
          {comparison.ratio !== null && comparison.percent !== null ? (
            <>
              Compared with “{comparison.name}”: {turnover.name} : {comparison.name} ={' '}
              {comparison.percent.toFixed(1)} : {(100 - comparison.percent).toFixed(1)} (TOF ratio{' '}
              {scientific(comparison.ratio)}), ΔΔG‡<sub>eff</sub> = {formatDelta(comparison.ddg, settings)} {unit}
            </>
          ) : (
            <span className="muted">{comparison.message}</span>
          )}
        </p>
      )}
      {result.profile && (
        <div className="turnover-chart">
          <ProfileChart
            data={result.profile}
            colours={[colour]}
            names={[turnover.name]}
            settings={settings}
            svgRef={svgRef}
            marks={marks}
          />
        </div>
      )}
      {result.table && (
        <>
          <div className="section-head">
            <h3>Degree of TOF control</h3>
            <button
              className="small"
              onClick={() => download(`/api/turnovers/${turnover.id}/table.csv`, 'turnover.csv')}
            >
              Export CSV
            </button>
          </div>
          <table className="energy-table" aria-label="Degree of TOF control">
            <thead>
              <tr>
                {result.table.columns.map((c) => (
                  <th key={c}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.table.rows.map((row, i) => (
                <tr key={i} className={row[7] ? 'determining' : undefined}>
                  {row.map((cell, j) => (
                    <td key={j} className={j >= 5 && j <= 6 ? 'num' : undefined}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {result.notes.length > 0 && (
        <ul className="warnings" aria-label="Result notes">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function TurnoverEditor({
  turnover,
  turnovers,
  canvas,
  settings,
  energyOptions,
  refreshKey,
  onSaved,
  onDeleted,
  onSelectNode,
}: {
  turnover: Turnover
  turnovers: Turnover[]
  canvas: Canvas
  settings: Settings | null
  energyOptions: EnergyOptions | null
  refreshKey: number
  onSaved: (turnover: Turnover) => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
}) {
  const [result, setResult] = useState<TurnoverResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    let current = true
    api.turnoverResult(turnover.id).then(
      (found) => current && setResult(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [turnover, refreshKey])

  const save = (fields: TurnoverFields) =>
    api.updateTurnover(turnover.id, fields).then(
      (saved) => {
        setError(null)
        onSaved(saved)
      },
      (err: unknown) => setError(errorText(err)),
    )

  const takeBranch = (branchId: string) => {
    const branch = canvas.branches.find((b) => b.id === branchId)
    api.branchPathway(branchId).then(
      (found) =>
        found.closed
          ? save({ path: found.path })
          : setError(
              `The pathway of “${branch?.name || 'Unnamed branch'}” does not close a cycle here. Close it in the energy drawer and use “Turnover” on it there.`,
            ),
      (err: unknown) => setError(errorText(err)),
    )
  }

  const names = new Map<string, string>([
    ...canvas.nodes.map((n) => [n.id, n.label || 'Untitled node'] as [string, string]),
    ...canvas.groups.map((g) => [g.id, `${g.label || 'Group'} (group)`] as [string, string]),
  ])
  const level = energyOptions?.levels.find((l) => l.key === turnover.level)
  const types = level?.types.length
    ? ENERGY_TYPES.filter((t) => level.types.includes(t) || t === turnover.energy_type)
    : ENERGY_TYPES
  const others = turnovers.filter((t) => t.id !== turnover.id)

  return (
    <div className="inspector" aria-label="Turnover">
      <div className="inspector-head">
        <h2>{turnover.name}</h2>
        <button className="danger" onClick={() => setDeleting(true)}>
          Delete…
        </button>
      </div>
      <section aria-label="Cycle">
        <h3>Cycle</h3>
        {turnover.path.length ? (
          <p className="pathway-nodes" aria-label="Pathway">
            {turnover.path.map((id, i) => (
              <span key={i}>
                {i > 0 && ' → '}
                <button className="link" onClick={() => onSelectNode(id)}>
                  {names.get(id) ?? 'a deleted node'}
                </button>
                {i === turnover.path.length - 1 && closesCycle(turnover.path) && (
                  <span className="cycle-mark" title="Cycle closed">
                    {' '}
                    ↻
                  </span>
                )}
              </span>
            ))}
          </p>
        ) : (
          <p className="muted">No pathway yet.</p>
        )}
        <select aria-label="Take a branch's pathway" value="" onChange={(event) => takeBranch(event.target.value)}>
          <option value="" disabled>
            Take a branch’s pathway…
          </option>
          {canvas.branches.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name || 'Unnamed branch'}
            </option>
          ))}
        </select>
        <p className="muted small">
          A cycle is a pathway that returns to an earlier node (↻). Build one in the energy drawer and choose “Turnover”
          on it, or take a branch’s pathway when it closes. Points before the node it returns to are left out.
        </p>
      </section>
      <section className="fields" aria-label="Turnover settings">
        <TextField key={turnover.name} label="Name" value={turnover.name} onCommit={(name) => save({ name })} />
        <div className="field-pair">
          <label className="field">
            <span>Level of theory</span>
            <select
              aria-label="Turnover level"
              value={turnover.level ?? ''}
              onChange={(event) => save({ level: event.target.value || null })}
            >
              {!level && <option value="">{turnover.level ? 'A level no longer listed' : 'Choose…'}</option>}
              {energyOptions?.levels.map((l) => (
                <option key={l.key} value={l.key}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Energy</span>
            <select
              aria-label="Turnover energy type"
              value={turnover.energy_type}
              onChange={(event) => save({ energy_type: event.target.value as EnergyType })}
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {energyTypeName(t, result?.temperature, result?.cutoff)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-pair">
          <label className="field">
            <span>Temperature (K)</span>
            <NumberInput
              key={String(turnover.temperature)}
              label="Turnover temperature"
              value={turnover.temperature}
              positive
              placeholder={`${settings?.qh_temperature ?? 298.15} (Settings)`}
              onCommit={(temperature) => save({ temperature })}
            />
          </label>
          <label className="field">
            <span>Compare with</span>
            <select
              aria-label="Compare with"
              value={turnover.compare_id ?? ''}
              onChange={(event) => save({ compare_id: event.target.value || null })}
            >
              <option value="">No other cycle</option>
              {others.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted small">
          Leave the temperature empty to use the G_qh temperature in Settings. Each point is relative to the cycle’s
          first node, with the free species that join and leave along the way, so the closing point is the reaction
          energy.
        </p>
      </section>
      {error && <p role="alert">{error}</p>}
      {result && (
        <TurnoverResultView
          turnover={turnover}
          result={result}
          canvas={canvas}
          settings={settings}
          onSelectNode={onSelectNode}
        />
      )}
      <Notes key={turnover.notes} notes={turnover.notes} onSave={(notes) => save({ notes })} />
      {deleting && (
        <Modal
          title="Delete turnover"
          onClose={() => setDeleting(false)}
          actions={
            <>
              <button onClick={() => setDeleting(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() =>
                  api.deleteTurnover(turnover.id).then(
                    () => {
                      setDeleting(false)
                      onDeleted()
                    },
                    (err: unknown) => {
                      setDeleting(false)
                      setError(errorText(err))
                    },
                  )
                }
              >
                Delete
              </button>
            </>
          }
        >
          <p>
            Delete “{turnover.name}”? The nodes and their calculations are kept; the deletion is recorded in the
            history.
          </p>
        </Modal>
      )}
    </div>
  )
}

/** S7: the saved analyses of the investigation. */
export function AnalysesView({
  canvas,
  settings,
  energyOptions,
  defaultLevel,
  refreshKey,
  openId,
  onSelectNode,
  onChanged,
}: {
  canvas: Canvas
  settings: Settings | null
  energyOptions: EnergyOptions | null
  /** The energy view's level, for a new selectivity or turnover. */
  defaultLevel: string | null
  refreshKey: number
  /** A selectivity or turnover to show first, such as one just created from a selection or a
   * pathway; the parent remounts the view (key) when it changes. */
  openId: string | null
  onSelectNode: (id: string) => void
  /** A change that belongs in the history. */
  onChanged: () => void
}) {
  const [list, setList] = useState<Selectivity[] | null>(null)
  const [turnovers, setTurnovers] = useState<Turnover[] | null>(null)
  const [chosen, setChosen] = useState<string | null>(openId)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    () =>
      Promise.all([api.selectivities(), api.turnovers()]).then(
        ([selectivities, cycles]) => {
          setList(selectivities)
          setTurnovers(cycles)
        },
        (err: unknown) => setError(errorText(err)),
      ),
    [],
  )
  useEffect(() => {
    load()
  }, [load, refreshKey])

  const freeName = (taken: Set<string>, base: string, start: number) => {
    let n = start
    while (taken.has(`${base} ${n}`)) n++
    return `${base} ${n}`
  }

  const create = () => {
    const name = freeName(new Set((list ?? []).map((s) => s.name)), 'Selectivity', (list?.length ?? 0) + 1)
    api
      .createSelectivity({
        name,
        level: defaultLevel,
        outcomes: [
          { name: 'Outcome 1', members: [], experimental: null },
          { name: 'Outcome 2', members: [], experimental: null },
        ],
      })
      .then(
        (created) => {
          setList((current) => [...(current ?? []), created])
          setChosen(created.id)
          onChanged()
        },
        (err: unknown) => setError(errorText(err)),
      )
  }

  const createTurnover = () => {
    const name = freeName(new Set((turnovers ?? []).map((t) => t.name)), 'Turnover', (turnovers?.length ?? 0) + 1)
    api.createTurnover({ name, level: defaultLevel }).then(
      (created) => {
        setTurnovers((current) => [...(current ?? []), created])
        setChosen(created.id)
        onChanged()
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const shownTurnover = turnovers?.find((t) => t.id === chosen) ?? null
  const shown = shownTurnover ? null : (list?.find((s) => s.id === chosen) ?? list?.[0] ?? null)
  const fallbackTurnover = shown || shownTurnover ? null : (turnovers?.[0] ?? null)
  const turnover = shownTurnover ?? fallbackTurnover
  const loaded = list !== null && turnovers !== null
  return (
    <div className="workspace analyses">
      <nav className="outline" aria-label="Analyses">
        <div className="outline-section">
          <div className="section-head">
            <h3>Selectivity</h3>
            <button className="small" onClick={create} aria-label="New selectivity">
              New
            </button>
          </div>
          {list && list.length === 0 && (
            <p className="muted small">
              ΔΔG‡ and the predicted ratio from competing transition states. Select the TSs or groups on the canvas and
              choose “Selectivity…”, or start a new one here.
            </p>
          )}
          <ul className="plain">
            {list?.map((s) => (
              <li key={s.id}>
                <button className="link" aria-current={shown?.id === s.id} onClick={() => setChosen(s.id)}>
                  {s.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="outline-section">
          <div className="section-head">
            <h3>Turnover</h3>
            <button className="small" onClick={createTurnover} aria-label="New turnover">
              New
            </button>
          </div>
          {turnovers && turnovers.length === 0 && (
            <p className="muted small">
              The TOF of a closed catalytic cycle and the states that determine it (energetic span). Close a pathway in
              the energy drawer and choose “Turnover”, or start a new one here.
            </p>
          )}
          <ul className="plain">
            {turnovers?.map((t) => (
              <li key={t.id}>
                <button className="link" aria-current={turnover?.id === t.id} onClick={() => setChosen(t.id)}>
                  {t.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
        {error && <p role="alert">{error}</p>}
      </nav>
      <main className="main">
        {shown ? (
          <SelectivityEditor
            key={shown.id}
            selectivity={shown}
            canvas={canvas}
            settings={settings}
            energyOptions={energyOptions}
            refreshKey={refreshKey}
            onSaved={(saved) => {
              setList((current) => (current ?? []).map((s) => (s.id === saved.id ? saved : s)))
              onChanged()
            }}
            onDeleted={() => {
              setList((current) => (current ?? []).filter((s) => s.id !== shown.id))
              setChosen(null)
              onChanged()
            }}
            onSelectNode={onSelectNode}
          />
        ) : turnover ? (
          <TurnoverEditor
            key={turnover.id}
            turnover={turnover}
            turnovers={turnovers ?? []}
            canvas={canvas}
            settings={settings}
            energyOptions={energyOptions}
            refreshKey={refreshKey}
            onSaved={(saved) => {
              setTurnovers((current) => (current ?? []).map((t) => (t.id === saved.id ? saved : t)))
              onChanged()
            }}
            onDeleted={() => {
              // Deleting a turnover clears comparisons with it (D86).
              setChosen(null)
              load()
              onChanged()
            }}
            onSelectNode={onSelectNode}
          />
        ) : (
          loaded && <p className="placeholder muted">No analyses yet.</p>
        )}
      </main>
    </div>
  )
}
