import { useEffect, useState } from 'react'
import {
  api,
  energyTypeName,
  formatDelta,
  type Canvas,
  type EnergyOptions,
  type EnergyType,
  type Equilibrium,
  type EquilibriumFields,
  type EquilibriumMember,
  type EquilibriumNextStep,
  type EquilibriumResult,
  type EquilibriumSet,
  type Selectivity,
  type Settings,
} from '../api'
import { errorText, scientific } from '../util'
import { NumberInput } from './AnalysisParts'
import { Notes, TextField } from './Fields'
import { Modal } from './Modal'

// D171: a conformer equilibrium: the populations of conformers the user chose, the Eyring
// rates of the TSs between them and the time the set takes to reach equilibrium.

const ENERGY_TYPES: EnergyType[] = ['E', 'H', 'G', 'G_qh']
// The branch palette, so each conformer keeps one colour in the tables and the chart.
const COLOURS = ['#2459c6', '#d9480f', '#2b8a3e', '#c2255c', '#7048e8', '#0b7285', '#e67700', '#5c940d', '#862e9c', '#495057']

const UNITS: [number, string][] = [
  [365.25 * 86400, 'years'],
  [86400, 'days'],
  [3600, 'h'],
  [60, 'min'],
  [1, 's'],
  [1e-3, 'ms'],
  [1e-6, 'µs'],
  [1e-9, 'ns'],
  [1e-12, 'ps'],
  [1e-15, 'fs'],
]

/** 7.1e-8 as "71 ns"; beyond a thousand years in scientific seconds. */
function duration(seconds: number | null): string {
  if (seconds === null) return 'n/a'
  if (seconds === 0) return '0 s'
  if (seconds >= 1000 * UNITS[0][0]) return `${scientific(seconds)} s`
  const [size, name] = UNITS.find(([size]) => seconds >= size) ?? UNITS[UNITS.length - 1]
  const value = seconds / size
  return `${value >= 100 ? value.toFixed(0) : value.toPrecision(2)} ${name}`
}

function decadeLabel(power: number): string {
  const seconds = 10 ** power
  const [size, name] = UNITS.find(([size]) => seconds >= size * 0.999) ?? UNITS[UNITS.length - 1]
  const value = seconds / size
  return value >= 1 && value < 1000 && Number.isInteger(Math.round(value * 1000) / 1000)
    ? `${Math.round(value)} ${name}`
    : `${scientific(seconds)} s`
}

/** D171f: the populations of one set against log time, from its start conformer. */
function PopulationChart({
  set,
  labels,
  colours,
}: {
  set: EquilibriumSet
  labels: Map<string, string>
  colours: Map<string, string>
}) {
  if (!set.curve) return null
  const { times, populations } = set.curve
  const W = 560
  const H = 240
  const left = 44
  const right = 12
  const top = 12
  const bottom = 34
  const lo = Math.log10(times[0])
  const hi = Math.log10(times[times.length - 1])
  const x = (t: number) => left + ((Math.log10(t) - lo) / (hi - lo || 1)) * (W - left - right)
  const y = (p: number) => top + (1 - p) * (H - top - bottom)
  const decades: number[] = []
  for (let d = Math.ceil(lo); d <= Math.floor(hi); d++) decades.push(d)
  const step = Math.max(1, Math.ceil(decades.length / 7))
  return (
    <figure className="equilibrium-chart">
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="Populations over time">
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <g key={p}>
            <line x1={left} x2={W - right} y1={y(p)} y2={y(p)} stroke={p === 0 ? 'var(--text-faint)' : 'var(--chart-grid)'} />
            <text x={left - 6} y={y(p) + 4} textAnchor="end" fontSize="11" fill="var(--muted)">
              {p * 100}%
            </text>
          </g>
        ))}
        {decades
          .filter((d) => (d - decades[0]) % step === 0)
          .map((d) => (
            <g key={d}>
              <line x1={x(10 ** d)} x2={x(10 ** d)} y1={top} y2={H - bottom} stroke="var(--chart-grid)" />
              <text x={x(10 ** d)} y={H - bottom + 16} textAnchor="middle" fontSize="11" fill="var(--muted)">
                {decadeLabel(d)}
              </text>
            </g>
          ))}
        {set.equilibrium_time !== null && set.equilibrium_time > times[0] && (
          <line
            x1={x(set.equilibrium_time)}
            x2={x(set.equilibrium_time)}
            y1={top}
            y2={H - bottom}
            stroke="var(--text-faint)"
            strokeDasharray="4 4"
          >
            <title>Within 1 percentage point of equilibrium after {duration(set.equilibrium_time)}</title>
          </line>
        )}
        {set.conformer_ids.map((id, i) => (
          <polyline
            key={id}
            fill="none"
            stroke={colours.get(id)}
            strokeWidth={2}
            points={times.map((t, j) => `${x(t).toFixed(1)},${y(populations[i][j]).toFixed(1)}`).join(' ')}
          >
            <title>{labels.get(id)}</title>
          </polyline>
        ))}
        <text x={(left + W - right) / 2} y={H - 4} textAnchor="middle" fontSize="11" fill="var(--muted)">
          time from pure {labels.get(set.start_id ?? '')} (log scale)
        </text>
      </svg>
      <figcaption className="legend">
        {set.conformer_ids.map((id) => (
          <span key={id}>
            <span className="swatch" style={{ background: colours.get(id) }} /> {labels.get(id)}
          </span>
        ))}
      </figcaption>
    </figure>
  )
}

/** D173: the next step's TSs from the pool of the lowest conformer's set (Curtin–Hammett). */
function NextStepView({
  found,
  lowest,
  settings,
  onSelectNode,
}: {
  found: EquilibriumNextStep
  lowest: string
  settings: Settings | null
  onSelectNode: (id: string) => void
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  return (
    <section aria-label="Next step">
      <h3>Next step (Curtin–Hammett)</h3>
      {found.members.length > 0 && (
        <table className="energy-table" aria-label="Next step TSs">
          <thead>
            <tr>
              <th>TS</th>
              <th>Free species</th>
              <th>ΔG‡ from {lowest} ({unit})</th>
            </tr>
          </thead>
          <tbody>
            {found.members.map((m) => (
              <tr key={m.id} className={m.barrier === null ? 'muted' : undefined}>
                <td>
                  <button className="link" onClick={() => onSelectNode(m.id)}>
                    {m.label}
                  </button>
                </td>
                <td>
                  {m.species
                    .map((s) => `${s.count > 0 ? '+' : '−'}${Math.abs(s.count) > 1 ? `${Math.abs(s.count)} ` : ''}${s.label}`)
                    .join(', ')}
                </td>
                <td className="num">{m.barrier === null ? m.message : formatDelta(m.barrier, settings)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {found.effective_barrier !== null && (
        <p>
          <strong>
            Next step: effective barrier {formatDelta(found.effective_barrier, settings)} {unit}, k ={' '}
            {scientific(found.rate ?? 0)} s⁻¹, 1/k = {duration(found.time)}
          </strong>
          {found.interconversion_time !== null && (
            <>
              {' '}
              <span className="muted">
                against interconversion of the conformers in {duration(found.interconversion_time)} (slowest relaxation
                time)
              </span>
            </>
          )}
        </p>
      )}
      {found.faster !== null && found.ratio !== null && found.ddg !== null && (
        <p className="notice" role="status" aria-label="Curtin–Hammett check">
          {found.faster === 'interconversion'
            ? `The conformers interconvert ${scientific(found.ratio)} times faster than the next step (ΔΔG‡ ${formatDelta(found.ddg, settings)} ${unit}): the Curtin–Hammett condition holds, so the next step's selectivity follows its TSs alone.`
            : `The next step is ${scientific(1 / found.ratio)} times faster than the conformers interconvert (ΔΔG‡ ${formatDelta(-found.ddg, settings)} ${unit}): Curtin–Hammett does not hold, and each conformer reacts mostly before it exchanges.`}
        </p>
      )}
      {found.message && <p className="muted">{found.message}</p>}
      {found.notes.length > 0 && (
        <ul className="warnings" aria-label="Next step notes">
          {found.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function EquilibriumResultView({
  result,
  settings,
  onSelectNode,
}: {
  result: EquilibriumResult
  settings: Settings | null
  onSelectNode: (id: string) => void
}) {
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const labels = new Map(result.conformers.map((c) => [c.id, c.label]))
  const colours = new Map(result.conformers.map((c, i) => [c.id, COLOURS[i % COLOURS.length]]))
  const typeName = energyTypeName(
    result.energy_type,
    result.temperature,
    result.cutoff,
    result.standard_state,
    result.job_temperatures,
  )
  const ok = result.status === 'ok'
  const link = (id: string) => (
    <button className="link" onClick={() => onSelectNode(id)}>
      {labels.get(id) ?? id}
    </button>
  )
  return (
    <section aria-label="Conformer equilibrium result">
      <h3>Result</h3>
      {result.level_label && (
        <p className="muted small">
          {result.level_label} · {typeName} · {result.temperature} K
          {result.temperature_from_settings ? ' (this investigation’s G_qh temperature, in Settings)' : ''}
        </p>
      )}
      {!ok && (
        <p className="notice" role="status">
          {result.status === 'n/a' ? 'n/a: ' : ''}
          {result.message}
        </p>
      )}
      {ok && (
        <>
          <table className="energy-table" aria-label="Populations">
            <thead>
              <tr>
                <th>Conformer</th>
                <th>Count</th>
                <th>
                  Δ{result.energy_type} ({unit})
                </th>
                <th>Population</th>
                <th>Effective barrier ({unit})</th>
              </tr>
            </thead>
            <tbody>
              {result.conformers.map((c) => (
                <tr key={c.id}>
                  <td>
                    <span className="swatch" style={{ background: colours.get(c.id) }} /> {link(c.id)}
                  </td>
                  <td className="num">{c.count}</td>
                  <td className="num">{formatDelta(c.relative, settings)}</td>
                  <td className="num">{c.population >= 0.1 ? `${c.population.toFixed(1)} %` : `${scientific(c.population)} %`}</td>
                  <td className="num" title={c.barrier_from_id ? `From ${labels.get(c.barrier_from_id)}` : undefined}>
                    {c.effective_barrier === null ? '—' : formatDelta(c.effective_barrier, settings)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.barriers.length > 0 && (
            <table className="energy-table" aria-label="Barriers">
              <thead>
                <tr>
                  <th>TS</th>
                  <th>From</th>
                  <th>To</th>
                  <th>ΔG‡ ({unit})</th>
                  <th>k (s⁻¹)</th>
                  <th>t½</th>
                </tr>
              </thead>
              <tbody>
                {result.barriers.flatMap((b) =>
                  b.directions.length
                    ? b.directions.map((d, i) => (
                        <tr key={`${b.id}-${i}`}>
                          <td>{i === 0 && <button className="link" onClick={() => onSelectNode(b.id)}>{b.label}</button>}</td>
                          <td>{labels.get(d.from_id)}</td>
                          <td>{labels.get(d.to_id)}</td>
                          <td className="num">{formatDelta(d.barrier, settings)}</td>
                          <td className="num">{scientific(d.rate)}</td>
                          <td className="num">{duration(d.half_life)}</td>
                        </tr>
                      ))
                    : [
                        <tr key={b.id} className="muted">
                          <td>
                            <button className="link" onClick={() => onSelectNode(b.id)}>
                              {b.label}
                            </button>
                          </td>
                          <td colSpan={5}>joins no two conformers</td>
                        </tr>,
                      ],
                )}
              </tbody>
            </table>
          )}
          {result.sets.map((set, i) =>
            set.conformer_ids.length < 2 ? null : (
              <div key={i} aria-label="Equilibration">
                <p>
                  <strong>
                    {set.equilibrium_time === null
                      ? 'Time to equilibrium: too slow to resolve'
                      : `Equilibrium within 1 percentage point after ${duration(set.equilibrium_time)}`}
                  </strong>{' '}
                  <span className="muted">
                    from pure {labels.get(set.start_id ?? '')}
                    {set.start_chosen ? '' : ' (the slowest start)'}
                    {result.sets.filter((s) => s.conformer_ids.length > 1).length > 1 &&
                      ` · set of ${set.conformer_ids.map((id) => labels.get(id)).join(', ')}`}
                  </span>
                </p>
                <p className="muted small">
                  Relaxation times: {set.relaxation_times.map(duration).join(', ')}
                  {set.unresolved > 0 && `, and ${set.unresolved} too slow to resolve`}
                </p>
                <PopulationChart set={set} labels={labels} colours={colours} />
              </div>
            ),
          )}
          {result.next_step && (
            <NextStepView
              found={result.next_step}
              lowest={labels.get(result.lowest_id ?? '') ?? 'the lowest conformer'}
              settings={settings}
              onSelectNode={onSelectNode}
            />
          )}
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

/** D171h: members by hand, or from a branch, a group or a tag. */
function AddMembers({ canvas, members, onAdd }: { canvas: Canvas; members: EquilibriumMember[]; onAdd: (ids: string[]) => void }) {
  const tags = [...new Set(canvas.nodes.flatMap((n) => n.tags))].sort()
  const have = new Set(members.map((m) => m.id))
  const add = (choice: string) => {
    const [kind, id] = [choice.slice(0, choice.indexOf(':')), choice.slice(choice.indexOf(':') + 1)]
    const ids =
      kind === 'branch'
        ? canvas.nodes.filter((n) => n.branch_id === id).map((n) => n.id)
        : kind === 'tag'
          ? canvas.nodes.filter((n) => n.tags.includes(id)).map((n) => n.id)
          : [id]
    onAdd(ids.filter((i) => !have.has(i)))
  }
  return (
    <select aria-label="Add members" value="" onChange={(event) => add(event.target.value)}>
      <option value="" disabled>
        Add members…
      </option>
      <optgroup label="A branch’s nodes">
        {canvas.branches.map((b) => (
          <option key={b.id} value={`branch:${b.id}`}>
            {b.name || 'Unnamed branch'}
          </option>
        ))}
      </optgroup>
      <optgroup label="A group">
        {canvas.groups.map((g) => (
          <option key={g.id} value={`group:${g.id}`} disabled={have.has(g.id)}>
            {g.label || 'Group'}
          </option>
        ))}
      </optgroup>
      {tags.length > 0 && (
        <optgroup label="Nodes with a tag">
          {tags.map((t) => (
            <option key={t} value={`tag:${t}`}>
              {t}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="A node">
        {canvas.nodes
          .filter((n) => n.kind === 'node')
          .map((n) => (
            <option key={n.id} value={`node:${n.id}`} disabled={have.has(n.id)}>
              {n.label || 'Untitled node'}
            </option>
          ))}
      </optgroup>
    </select>
  )
}

/** D173: the next step's TSs: a node, a group, a branch's TS nodes or a selectivity's TSs. */
function AddNext({
  canvas,
  have,
  selectivities,
  onAdd,
}: {
  canvas: Canvas
  have: string[]
  selectivities: Selectivity[]
  onAdd: (ids: string[]) => void
}) {
  const taken = new Set(have)
  const ts = canvas.nodes.filter((n) => n.kind === 'node' && n.role === 'transition_state')
  const add = (choice: string) => {
    const [kind, id] = [choice.slice(0, choice.indexOf(':')), choice.slice(choice.indexOf(':') + 1)]
    const ids =
      kind === 'branch'
        ? ts.filter((n) => n.branch_id === id).map((n) => n.id)
        : kind === 'selectivity'
          ? (selectivities.find((s) => s.id === id)?.outcomes.flatMap((o) => o.members) ?? [])
          : [id]
    onAdd([...new Set(ids)].filter((i) => !taken.has(i)))
  }
  return (
    <select aria-label="Add next step TSs" value="" onChange={(event) => add(event.target.value)}>
      <option value="" disabled>
        Add TSs of the next step…
      </option>
      {selectivities.length > 0 && (
        <optgroup label="A selectivity’s TSs">
          {selectivities.map((s) => (
            <option key={s.id} value={`selectivity:${s.id}`}>
              {s.name}
            </option>
          ))}
        </optgroup>
      )}
      <optgroup label="A branch’s TS nodes">
        {canvas.branches.map((b) => (
          <option key={b.id} value={`branch:${b.id}`}>
            {b.name || 'Unnamed branch'}
          </option>
        ))}
      </optgroup>
      <optgroup label="A group">
        {canvas.groups.map((g) => (
          <option key={g.id} value={`group:${g.id}`} disabled={taken.has(g.id)}>
            {g.label || 'Group'}
          </option>
        ))}
      </optgroup>
      <optgroup label="A TS node">
        {ts.map((n) => (
          <option key={n.id} value={`node:${n.id}`} disabled={taken.has(n.id)}>
            {n.label || 'Untitled node'}
          </option>
        ))}
      </optgroup>
    </select>
  )
}

export function EquilibriumEditor({
  equilibrium,
  canvas,
  settings,
  energyOptions,
  refreshKey,
  onSaved,
  onDeleted,
  onSelectNode,
}: {
  equilibrium: Equilibrium
  canvas: Canvas
  settings: Settings | null
  energyOptions: EnergyOptions | null
  refreshKey: number
  onSaved: (equilibrium: Equilibrium) => void
  onDeleted: () => void
  onSelectNode: (id: string) => void
}) {
  const [result, setResult] = useState<EquilibriumResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [selectivities, setSelectivities] = useState<Selectivity[]>([])

  useEffect(() => {
    let current = true
    api.selectivities().then(
      (found) => current && setSelectivities(found),
      () => undefined,
    )
    return () => {
      current = false
    }
  }, [refreshKey])

  useEffect(() => {
    let current = true
    api.equilibriumResult(equilibrium.id).then(
      (found) => current && setResult(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [equilibrium, refreshKey])

  const save = (fields: EquilibriumFields) =>
    api.updateEquilibrium(equilibrium.id, fields).then(
      (saved) => {
        setError(null)
        onSaved(saved)
      },
      (err: unknown) => setError(errorText(err)),
    )
  const setMembers = (members: EquilibriumMember[]) => save({ members })

  const nodes = new Map(canvas.nodes.map((n) => [n.id, n]))
  const groups = new Map(canvas.groups.map((g) => [g.id, g]))
  const describe = (id: string): [string, string] => {
    const node = nodes.get(id)
    if (node) return [node.label || 'Untitled node', node.role === 'transition_state' ? 'TS' : 'conformer']
    const group = groups.get(id)
    if (group) return [group.label || 'Group', 'group']
    return ['a deleted node', '']
  }
  const conformerIds = equilibrium.members
    .flatMap((m) => (groups.has(m.id) ? canvas.nodes.filter((n) => n.group_id === m.id).map((n) => n.id) : [m.id]))
    .filter((id) => nodes.get(id) && nodes.get(id)?.role !== 'transition_state')
  const level = energyOptions?.levels.find((l) => l.key === equilibrium.level)
  const types = level?.types.length
    ? ENERGY_TYPES.filter((t) => level.types.includes(t) || t === equilibrium.energy_type)
    : ENERGY_TYPES

  return (
    <div className="inspector" aria-label="Conformer equilibrium">
      <div className="inspector-head">
        <h2>{equilibrium.name}</h2>
        <button className="danger" onClick={() => setDeleting(true)}>
          Delete…
        </button>
      </div>
      <section aria-label="Members">
        <h3>Conformers and TSs</h3>
        {equilibrium.members.length ? (
          <table className="energy-table" aria-label="Members">
            <thead>
              <tr>
                <th>Member</th>
                <th>Counts as</th>
                <th title="How many equivalent structures it stands for (mirror images, symmetry-equivalent rotamers drawn once)">
                  Count
                </th>
                <th />
              </tr>
            </thead>
            <tbody>
              {equilibrium.members.map((m, i) => {
                const [label, kind] = describe(m.id)
                return (
                  <tr key={m.id}>
                    <td>
                      <button className="link" onClick={() => onSelectNode(m.id)}>
                        {label}
                      </button>
                    </td>
                    <td>{kind}</td>
                    <td>
                      <input
                        aria-label={`Count of ${label}`}
                        className="count"
                        type="number"
                        min={1}
                        max={100}
                        step={1}
                        defaultValue={m.count}
                        onBlur={(event) => {
                          const count = Number(event.target.value)
                          if (Number.isInteger(count) && count >= 1 && count !== m.count)
                            setMembers(equilibrium.members.map((o, j) => (j === i ? { ...o, count } : o)))
                        }}
                      />
                    </td>
                    <td>
                      <button
                        className="small"
                        aria-label={`Remove ${label}`}
                        onClick={() => setMembers(equilibrium.members.filter((_, j) => j !== i))}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <p className="muted">No members yet.</p>
        )}
        <AddMembers
          canvas={canvas}
          members={equilibrium.members}
          onAdd={(ids) => ids.length && setMembers([...equilibrium.members, ...ids.map((id) => ({ id, count: 1 }))])}
        />
        <p className="muted small">
          A TS node joins the two conformers it has edges to; a direct edge between two conformers (no TS) joins
          nothing. Every other node counts as a conformer, so take out scans and other nodes without a value.
        </p>
      </section>
      <section aria-label="Next step TSs">
        <h3>Next step</h3>
        {equilibrium.next_ids.length > 0 && (
          <ul className="plain">
            {equilibrium.next_ids.map((id) => {
              const [label] = describe(id)
              return (
                <li key={id}>
                  <button className="link" onClick={() => onSelectNode(id)}>
                    {label}
                  </button>{' '}
                  <button
                    className="small"
                    aria-label={`Remove ${label} from the next step`}
                    onClick={() => save({ next_ids: equilibrium.next_ids.filter((o) => o !== id) })}
                  >
                    Remove
                  </button>
                </li>
              )
            })}
          </ul>
        )}
        <AddNext
          canvas={canvas}
          have={equilibrium.next_ids}
          selectivities={selectivities}
          onAdd={(ids) => ids.length && save({ next_ids: [...equilibrium.next_ids, ...ids] })}
        />
        <p className="muted small">
          The TSs of the step that follows, balanced by the free species from the lowest conformer. Their effective
          barrier is set beside how fast the conformers interconvert (Curtin–Hammett).
        </p>
      </section>
      <section className="fields" aria-label="Conformer equilibrium settings">
        <TextField key={equilibrium.name} label="Name" value={equilibrium.name} onCommit={(name) => save({ name })} />
        <div className="field-pair">
          <label className="field">
            <span>Level of theory</span>
            <select
              aria-label="Equilibrium level"
              value={equilibrium.level ?? ''}
              onChange={(event) => save({ level: event.target.value || null })}
            >
              {!level && <option value="">{equilibrium.level ? 'A level no longer listed' : 'Choose…'}</option>}
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
              aria-label="Equilibrium energy type"
              value={equilibrium.energy_type}
              onChange={(event) => save({ energy_type: event.target.value as EnergyType })}
            >
              {types.map((t) => (
                <option key={t} value={t}>
                  {energyTypeName(t, result?.temperature, result?.cutoff, result?.standard_state, result?.job_temperatures)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="field-pair">
          <label className="field">
            <span>Temperature (K)</span>
            <NumberInput
              key={String(equilibrium.temperature)}
              label="Equilibrium temperature"
              value={equilibrium.temperature}
              positive
              placeholder={`${settings?.qh_temperature ?? 298.15} (investigation)`}
              onCommit={(temperature) => save({ temperature })}
            />
          </label>
          <label className="field">
            <span>Start from</span>
            <select
              aria-label="Start conformer"
              value={equilibrium.start_id ?? ''}
              onChange={(event) => save({ start_id: event.target.value || null })}
            >
              <option value="">The slowest start</option>
              {conformerIds.map((id) => (
                <option key={id} value={id}>
                  {describe(id)[0]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>
      {error && <p role="alert">{error}</p>}
      {result && <EquilibriumResultView result={result} settings={settings} onSelectNode={onSelectNode} />}
      <Notes key={equilibrium.notes} notes={equilibrium.notes} onSave={(notes) => save({ notes })} />
      {deleting && (
        <Modal
          title="Delete conformer equilibrium"
          onClose={() => setDeleting(false)}
          actions={
            <>
              <button onClick={() => setDeleting(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() =>
                  api.deleteEquilibrium(equilibrium.id).then(
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
            Delete “{equilibrium.name}”? The nodes and their calculations are kept; the deletion is recorded in the
            history.
          </p>
        </Modal>
      )}
    </div>
  )
}
