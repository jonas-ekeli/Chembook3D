import { useEffect, useState } from 'react'
import {
  api,
  CALCULATION_TYPES,
  type Calculation,
  type Level,
  type LevelFields,
  type SourceFile,
} from '../api'
import { hartree } from '../util'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const LEVEL_FIELDS: { key: keyof LevelFields; label: string }[] = [
  { key: 'method', label: 'Method' },
  { key: 'basis', label: 'Basis set' },
  { key: 'dispersion', label: 'Dispersion' },
  { key: 'solvation_model', label: 'Solvation model' },
  { key: 'solvent', label: 'Solvent' },
]

/** FR-CALC-05: level fields can be edited; the parsed values stay visible (P13). */
function LevelEditor({
  calculation,
  levels,
  onSaved,
}: {
  calculation: Calculation
  levels: Level[]
  onSaved: (c: Calculation) => void
}) {
  const current = calculation.level
  const [draft, setDraft] = useState<LevelFields>({
    method: current?.method ?? '',
    basis: current?.basis ?? '',
    dispersion: current?.dispersion ?? '',
    solvation_model: current?.solvation_model ?? '',
    solvent: current?.solvent ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const parsed = calculation.parsed_level ?? {}
  const changed = LEVEL_FIELDS.some(({ key }) => draft[key] !== (current?.[key] ?? ''))

  return (
    <section aria-label="Level of theory">
      <h4>Level of theory</h4>
      <div className="level-grid">
        {LEVEL_FIELDS.map(({ key, label }) => (
          <label key={key} className="field">
            <span>{label}</span>
            <input
              aria-label={label}
              value={draft[key]}
              onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
            />
            {calculation.parsed_level && (parsed[key] ?? '') !== draft[key] && (
              <small className="muted">parsed: {String(parsed[key] ?? '') || '—'}</small>
            )}
          </label>
        ))}
      </div>
      {changed && (
        <button
          onClick={() =>
            api.updateCalculation(calculation.id, draft).then(onSaved, (err: unknown) => setError(errorText(err)))
          }
        >
          Save level
        </button>
      )}
      {calculation.type === 'single_point' && (
        <label className="field">
          <span>Geometry level (FR-CALC-04)</span>
          <select
            aria-label="Geometry level"
            value={calculation.geometry_level?.id ?? ''}
            onChange={(event) =>
              api
                .updateCalculation(calculation.id, { geometry_level_id: event.target.value || null })
                .then(onSaved, (err: unknown) => setError(errorText(err)))
            }
          >
            <option value="">Not set</option>
            {levels.map((level) => (
              <option key={level.id} value={level.id}>
                {level.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}

/** FR-FILE-02, FR-FILE-03: the copied file with its origin; name and path are editable. */
function SourceFileBox({ source, onChanged }: { source: SourceFile; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const save = (field: 'origin_device' | 'origin_path' | 'original_name', value: string) => {
    if (value.trim() === source[field]) return
    api.updateSourceFile(source.id, { [field]: value }).then(onChanged, (err: unknown) => setError(errorText(err)))
  }
  return (
    <section aria-label="Source file" className="source-file">
      <h4>Source file</h4>
      <div className="level-grid">
        <label className="field">
          <span>File name</span>
          <input aria-label="File name" defaultValue={source.original_name} onBlur={(e) => save('original_name', e.target.value)} />
        </label>
        <label className="field">
          <span>Device or server</span>
          <input aria-label="Device or server" defaultValue={source.origin_device} onBlur={(e) => save('origin_device', e.target.value)} />
        </label>
        <label className="field wide">
          <span>Original path</span>
          <input aria-label="Original path" defaultValue={source.origin_path} onBlur={(e) => save('origin_path', e.target.value)} />
        </label>
      </div>
      <p className="muted small">
        Copy: <code>{source.stored_path}</code> · imported {new Date(source.imported_at).toLocaleString()} · SHA-256{' '}
        <code title={source.checksum}>{source.checksum.slice(0, 12)}…</code>
      </p>
      {source.exists ? (
        <div className="buttons">
          <a className="button" href={api.sourceFileUrl(source.id)} download={source.original_name}>
            Download copy
          </a>
          <button onClick={() => api.openSourceFile(source.id).catch((err: unknown) => setError(errorText(err)))}>
            Open
          </button>
          <button onClick={() => api.openSourceFile(source.id, true).catch((err: unknown) => setError(errorText(err)))}>
            Show in folder
          </button>
        </div>
      ) : (
        <p role="alert">The copied file is missing from the investigation folder.</p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}

/** Key results of a calculation (FR-CALC-01); also shown by the read-only copy (D79). */
export function ResultValues({ calculation }: { calculation: Pick<Calculation, 'result' | 'quasi_harmonic'> }) {
  const r = calculation.result
  if (!r) return null
  return (
    <dl className="values">
      <dt>E(SCF)</dt>
      <dd className="mono">{hartree(r.energy, 8)}</dd>
      {r.optimization_converged !== null && (
        <>
          <dt>Optimization</dt>
          <dd>
            {r.optimization_converged ? 'converged' : 'not converged'}, {r.geometry_count} geometries
          </dd>
        </>
      )}
      {r.zpe !== null && (
        <>
          <dt>ZPE</dt>
          <dd className="mono">{hartree(r.zpe)}</dd>
          <dt>Thermal corr. to H, G</dt>
          <dd className="mono">
            {hartree(r.h_corr)}, {hartree(r.g_corr)}
          </dd>
          <dt>H, G</dt>
          <dd className="mono">
            {hartree(r.h)}, {hartree(r.g)}
          </dd>
          <dt>T, P</dt>
          <dd>
            {r.temperature} K, {r.pressure} atm
          </dd>
          {calculation.quasi_harmonic && (
            <>
              <dt>
                G_qh corr. ({calculation.quasi_harmonic.temperature} K, {calculation.quasi_harmonic.cutoff} cm⁻¹)
              </dt>
              <dd className="mono" aria-label="Quasi-harmonic correction">
                {calculation.quasi_harmonic.correction !== null ? (
                  <>
                    {hartree(calculation.quasi_harmonic.correction)}{' '}
                    <span className="muted small">
                      (printed G corr. {hartree(r.g_corr)}; {calculation.quasi_harmonic.raised_modes} modes raised
                      {calculation.quasi_harmonic.imaginary_excluded
                        ? `, ${calculation.quasi_harmonic.imaginary_excluded} imaginary left out`
                        : ''}
                      )
                    </span>
                  </>
                ) : (
                  <span title={calculation.quasi_harmonic.code ?? undefined}>
                    n/a: {calculation.quasi_harmonic.message}
                  </span>
                )}
              </dd>
            </>
          )}
          <dt>Mass, symmetry number</dt>
          <dd>
            {r.molecular_mass} amu, σ = {r.symmetry_number ?? '—'} ({r.point_group ?? '—'})
          </dd>
        </>
      )}
      {r.frequencies.length > 0 && (
        <>
          <dt>Frequencies</dt>
          <dd>
            {r.frequencies.length} ({r.imaginary_count} imaginary):{' '}
            <span className="mono">
              {r.frequencies
                .slice(0, 12)
                .map((f) => f.toFixed(1))
                .join(', ')}
              {r.frequencies.length > 12 && ', …'}
            </span>{' '}
            cm⁻¹
          </dd>
        </>
      )}
    </dl>
  )
}

function Details({
  calculation,
  levels,
  onSaved,
  onChanged,
}: {
  calculation: Calculation
  levels: Level[]
  onSaved: (c: Calculation) => void
  onChanged: () => void
}) {
  return (
    <div className="calc-details">
      <p className="muted small">
        {calculation.program} {calculation.program_version}
        {calculation.step_index && calculation.step_count && (
          <> · step {calculation.step_index} of {calculation.step_count}</>
        )}
        {calculation.title && <> · “{calculation.title}”</>}
      </p>
      {calculation.route && <pre className="route">{calculation.route}</pre>}
      <ResultValues calculation={calculation} />
      <LevelEditor key={calculation.level?.id ?? 'none'} calculation={calculation} levels={levels} onSaved={onSaved} />
      {calculation.source_file && <SourceFileBox source={calculation.source_file} onChanged={onChanged} />}
    </div>
  )
}

/** Calculations on a node (FR-CALC-01): type, composite level (D31), key results and warnings. */
export function CalculationList({
  nodeId,
  refreshKey,
  onChanged,
}: {
  nodeId: string
  refreshKey: number
  onChanged: () => void
}) {
  const [calculations, setCalculations] = useState<Calculation[]>([])
  const [levels, setLevels] = useState<Level[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.calculations(nodeId).then(setCalculations, (err: unknown) => setError(errorText(err)))
    api.levels().then(setLevels, () => undefined)
  }, [nodeId, refreshKey])

  const saved = (updated: Calculation) => {
    setCalculations((list) => list.map((c) => (c.id === updated.id ? updated : c)))
    onChanged()
  }

  if (error) return <p role="alert">{error}</p>
  if (calculations.length === 0) return <p className="muted">No calculations yet. Import an output file onto this node.</p>
  return (
    <ul className="calculations" aria-label="Calculations">
      {calculations.map((c) => (
        <li key={c.id}>
          <button className="calc-row" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
            <span className="calc-type">{CALCULATION_TYPES[c.type] ?? c.type}</span>
            <span className="calc-level">
              {c.composite_label}
              {c.level_edited && <span className="badge">edited</span>}
            </span>
            <span className="mono">{hartree(c.result?.energy, 8)}</span>
            {c.result?.g !== null && c.result?.g !== undefined && <span className="mono">G {hartree(c.result.g)}</span>}
            {c.result?.imaginary_count !== null && c.result?.imaginary_count !== undefined && (
              <span>{c.result.imaginary_count} imag.</span>
            )}
            {c.warnings.map((w) => (
              <span key={w.code} className="badge warn" title={w.message}>
                {w.code}
              </span>
            ))}
          </button>
          {open === c.id && (
            <Details
              calculation={c}
              levels={levels}
              onSaved={saved}
              onChanged={() => {
                api.calculations(nodeId).then(setCalculations, () => undefined)
                onChanged()
              }}
            />
          )}
        </li>
      ))}
    </ul>
  )
}
