import { api, standardStateCorrection, type Settings, type StandardState } from '../api'
import { NumberSetting } from './NumberSetting'
import type { SettingSection } from './SettingsDialog'

function described(values: Settings['thermo_defaults']): string {
  return `${values.qh_temperature} K, cutoff ${values.qh_cutoff} cm⁻¹, ${values.standard_state}`
}

/** D159: the G_qh temperature and cutoff and the standard state, a section of the Settings
 * dialog. They are the open investigation's (saved and synced with it, each change in its
 * History), under "This investigation" (D183); with no investigation open they are the defaults
 * a new investigation starts with, a setting of this computer. `resetKey` names the
 * investigation, so the number fields start again from its values when another is opened. */
export function thermoSection({
  settings,
  investigationName,
  resetKey,
  onSaved,
}: {
  settings: Settings
  investigationName: string | null
  resetKey: string
  onSaved: (settings: Settings) => void
}): SettingSection {
  const own = settings.thermo_scope === 'investigation'
  const save = (fields: Parameters<typeof api.saveSettings>[0]) => api.saveSettings(fields).then(onSaved)
  const defaults = settings.thermo_defaults
  const differs =
    own &&
    (defaults.qh_temperature !== settings.qh_temperature ||
      defaults.qh_cutoff !== settings.qh_cutoff ||
      defaults.standard_state !== settings.standard_state)
  const name = investigationName ?? 'the open investigation'
  const correction = (
    standardStateCorrection(settings.qh_temperature) * settings.energy_factors[settings.energy_unit]
  ).toFixed(settings.energy_decimals[settings.energy_unit] + 2)
  return {
    id: 'thermochemistry',
    title: 'Thermochemistry',
    icon: 'flask',
    scope: own ? 'investigation' : 'computer',
    badge: own ? `Saved in ${name}` : 'New investigations',
    note: own ? (
      <>
        Thermochemistry is saved in <strong>{name}</strong> and synced with it; each change is recorded in its History.
      </>
    ) : (
      <>No investigation is open, so Thermochemistry holds what a new investigation starts with; each keeps its own.</>
    ),
    rows: [
      {
        name: 'Temperature',
        help: 'For the quasi-harmonic free energy G_qh. Every G_qh value is shown with this and the cutoff.',
        control: (
          <NumberSetting
            key={`${resetKey}:temperature`}
            label="Temperature (K)"
            unit="K"
            value={settings.qh_temperature}
            onSave={(qh_temperature) => save({ qh_temperature })}
          />
        ),
      },
      {
        name: 'Frequency cutoff',
        help: 'Lower frequencies are raised to it for the vibrational entropy only (Truhlar), as in the reference script.',
        control: (
          <NumberSetting
            key={`${resetKey}:cutoff`}
            label="Cutoff (cm⁻¹)"
            unit="cm⁻¹"
            value={settings.qh_cutoff}
            allowZero
            onSave={(qh_cutoff) => save({ qh_cutoff })}
          />
        ),
      },
      {
        name: 'Standard state of G and G_qh',
        help: (
          <>
            1 M adds RT ln(V<sub>m</sub> / 1 L mol⁻¹): {correction} {settings.energy_unit} at {settings.qh_temperature}{' '}
            K. It changes relative free energies only where species join or leave.
          </>
        ),
        more: (
          <>
            It is added to the free energy of every node and free species, with the ideal-gas molar volume V<sub>m</sub>{' '}
            at the temperature above, so it changes nothing where the number of molecules stays the same. G, read from
            the files, takes it at its own frequency job’s temperature (D160). E and H are not changed. A free species
            can be given a concentration of its own (1 M, neat solvent or any molarity) in its panel.
          </>
        ),
        control: (
          <select
            aria-label="Standard state"
            value={settings.standard_state}
            onChange={(event) => save({ standard_state: event.target.value as StandardState })}
          >
            <option value="1 atm">1 atm ideal gas (as in the output files)</option>
            <option value="1 M">1 M (1 mol/L)</option>
          </select>
        ),
      },
      ...(own
        ? [
            {
              name: 'New investigations',
              help: `New investigations start with ${described(defaults)}.`,
              control: differs ? (
                <button className="small wraps" onClick={() => save({ thermo_as_default: true })}>
                  Use this investigation’s for new ones
                </button>
              ) : (
                <span className="muted small">The same as this investigation’s</span>
              ),
            },
          ]
        : []),
    ],
  }
}
