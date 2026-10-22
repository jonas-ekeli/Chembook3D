import { useState } from 'react'
import {
  concentrationCorrection,
  concentrationText,
  type Concentration,
  type Node,
  type NodeFields,
  type Settings,
} from '../api'

const OTHER = '__other'

/** What is being typed before it is saved: another neat solvent, or a molarity. */
type Pending = { kind: 'neat' | 'molar'; solvent: string; molarity: string }

/** D160: the concentration a free species' G and G_qh are taken at wherever it joins or leaves:
 * the investigation's standard state (the default), 1 M, a neat solvent or any molarity. */
export function SpeciesConcentration({
  node,
  settings,
  onSave,
}: {
  node: Node
  settings: Settings | null
  onSave: (fields: NodeFields) => void
}) {
  const own = node.concentration ?? null
  const solvents = settings?.solvents ?? []
  const listed = own?.kind === 'neat' && solvents.some((s) => s.name === own.solvent)
  const [pending, setPending] = useState<Pending | null>(null)
  const save = (concentration: Concentration | null) => {
    setPending(null)
    onSave({ concentration })
  }
  // A saved neat solvent not in the table, or a saved molarity, shows as being typed.
  const typing: Pending | null =
    pending ??
    (own?.kind === 'molar'
      ? { kind: 'molar', solvent: '', molarity: String(own.molarity) }
      : own?.kind === 'neat' && !listed
        ? {
            kind: 'neat',
            solvent: own.solvent ?? '',
            molarity: String(own.molarity ?? ''),
          }
        : null)
  const choice = pending?.kind ?? own?.kind ?? 'default'
  const molarity = Number(typing?.molarity ?? '')
  const valid = !!typing && typing.molarity.trim() !== '' && Number.isFinite(molarity) && molarity > 0
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const factor = settings?.energy_factors[unit] ?? 1
  const decimals = settings?.energy_decimals[unit] ?? 2
  const temperature = settings?.qh_temperature ?? 298.15
  const standard = settings?.standard_state ?? '1 atm'

  return (
    <div className="species-concentration" role="group" aria-label="Species concentration">
      <h3>Concentration</h3>
      <label className="field">
        <span>G and G_qh at</span>
        <select
          aria-label="Concentration"
          value={choice}
          onChange={(event) => {
            const kind = event.target.value
            if (kind === 'default') save(null)
            else if (kind === '1 M') save({ kind: '1 M' })
            else if (kind === 'neat' && solvents.length) save({ kind: 'neat', solvent: solvents[0].name })
            else
              setPending({
                kind: kind === 'neat' ? 'neat' : 'molar',
                solvent: '',
                molarity: '',
              })
          }}
        >
          <option value="default">This investigation’s standard state ({standard})</option>
          <option value="1 M">1 M</option>
          <option value="neat">Neat solvent</option>
          <option value="molar">Another concentration</option>
        </select>
      </label>
      {choice === 'neat' && (
        <label className="field">
          <span>Solvent</span>
          <select
            aria-label="Neat solvent"
            value={typing ? OTHER : own?.kind === 'neat' ? (own.solvent ?? '') : ''}
            onChange={(event) =>
              event.target.value === OTHER
                ? setPending({ kind: 'neat', solvent: '', molarity: '' })
                : save({ kind: 'neat', solvent: event.target.value })
            }
          >
            {solvents.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name} ({s.molarity} M)
              </option>
            ))}
            <option value={OTHER}>Another solvent…</option>
          </select>
        </label>
      )}
      {typing?.kind === 'neat' && (
        <label className="field">
          <span>Solvent name</span>
          <input
            aria-label="Solvent name"
            value={typing.solvent}
            onChange={(event) => setPending({ ...typing, solvent: event.target.value })}
          />
        </label>
      )}
      {typing && (
        <label className="field">
          <span>{typing.kind === 'neat' ? 'Its molarity (mol/L)' : 'Concentration (mol/L)'}</span>
          <input
            aria-label={typing.kind === 'neat' ? 'Solvent molarity' : 'Concentration in mol/L'}
            inputMode="decimal"
            value={typing.molarity}
            aria-invalid={!valid}
            onChange={(event) => setPending({ ...typing, molarity: event.target.value })}
            onBlur={() => {
              if (!valid || pending === null) return
              save(
                typing.kind === 'neat'
                  ? {
                      kind: 'neat',
                      solvent: typing.solvent.trim() || null,
                      molarity,
                    }
                  : { kind: 'molar', molarity },
              )
            }}
          />
        </label>
      )}
      <p className="muted small">
        {own === null
          ? `Follows this investigation’s standard state (${standard}, in Settings).`
          : `${concentrationText(own)}: its G and G_qh add RT ln(V_m / 1 L mol⁻¹) + RT ln(c / 1 M) = ${(
              concentrationCorrection(temperature, own) * factor
            ).toFixed(decimals)} ${unit} at ${temperature} K wherever it joins or leaves, whatever the standard state.`}
      </p>
    </div>
  )
}
