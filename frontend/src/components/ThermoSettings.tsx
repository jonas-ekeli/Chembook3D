import { api, standardStateCorrection, type Settings, type StandardState } from '../api'
import { NumberSetting } from './NumberSetting'

function described(values: Settings['thermo_defaults']): string {
  return `${values.qh_temperature} K, cutoff ${values.qh_cutoff} cm⁻¹, ${values.standard_state}`
}

/** D159: the G_qh temperature and cutoff and the standard state in the Settings dialog. They
 * are the open investigation's (saved and synced with it, each change in its History); with no
 * investigation open they are the defaults a new investigation starts with. */
export function ThermoSettings({
  settings,
  investigationName,
  onSaved,
}: {
  settings: Settings
  investigationName: string | null
  onSaved: (settings: Settings) => void
}) {
  const own = settings.thermo_scope === 'investigation'
  const save = (fields: Parameters<typeof api.saveSettings>[0]) => api.saveSettings(fields).then(onSaved)
  const defaults = settings.thermo_defaults
  const differs =
    own &&
    (defaults.qh_temperature !== settings.qh_temperature ||
      defaults.qh_cutoff !== settings.qh_cutoff ||
      defaults.standard_state !== settings.standard_state)
  return (
    <section aria-label="Thermochemistry">
      <h3>{own ? 'Thermochemistry of this investigation' : 'Thermochemistry of new investigations'}</h3>
      <p className="muted small">
        {own ? (
          <>
            These belong to {investigationName ? <strong>{investigationName}</strong> : 'the open investigation'}: they
            are saved in it, travel with it when it is synced, and each change is recorded in its History.
          </>
        ) : (
          <>
            No investigation is open, so these are the values a new investigation starts with. Each investigation
            keeps its own, changed here while it is open.
          </>
        )}
      </p>
      <h3>Quasi-harmonic free energy (G_qh)</h3>
      <NumberSetting
        label="Temperature (K)"
        value={settings.qh_temperature}
        onSave={(qh_temperature) => save({ qh_temperature })}
      />
      <NumberSetting
        label="Cutoff (cm⁻¹)"
        value={settings.qh_cutoff}
        allowZero
        onSave={(qh_cutoff) => save({ qh_cutoff })}
      />
      <p className="muted small">
        Frequencies below the cutoff are raised to it for the vibrational entropy only (Truhlar), as in the
        reference script. Every G_qh value is shown with these two numbers.
      </p>
      <h3>Standard state of G and G_qh</h3>
      <label className="field">
        <span>Standard state</span>
        <select
          aria-label="Standard state"
          value={settings.standard_state}
          onChange={(event) => save({ standard_state: event.target.value as StandardState })}
        >
          <option value="1 atm">1 atm ideal gas (as in the output files)</option>
          <option value="1 M">1 M (1 mol/L)</option>
        </select>
      </label>
      <p className="muted small">
        1 M adds RT ln(V<sub>m</sub> / 1 L mol⁻¹) to the free energy of every node and free species, with the
        ideal-gas molar volume V<sub>m</sub> at the temperature above:{' '}
        {(standardStateCorrection(settings.qh_temperature) * settings.energy_factors[settings.energy_unit]).toFixed(
          settings.energy_decimals[settings.energy_unit] + 2,
        )}{' '}
        {settings.energy_unit} at {settings.qh_temperature} K. G, read from the files, takes it at its own frequency
        job’s temperature (D160). It changes relative free energies only where the number of molecules changes
        (species joining or leaving). E and H are not changed. A free species can be given a concentration of its own
        (1 M, neat solvent or any molarity) in its panel.
      </p>
      {own && (
        <p className="muted small" aria-label="Defaults for new investigations">
          New investigations start with {described(defaults)}.{' '}
          {differs && (
            <button className="small" onClick={() => save({ thermo_as_default: true })}>
              Use this investigation’s for new ones
            </button>
          )}
        </p>
      )}
    </section>
  )
}
