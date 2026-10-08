import { useState, type ReactNode } from 'react'
import type { ProfileStyle, Profiles, Settings } from '../api'
import { Modal } from './Modal'
import { FONTS } from '../profileStyle'
import { ProfileChart } from './ProfileChart'

type Choice<K extends keyof ProfileStyle> = [ProfileStyle[K], string][]

const CHOICES: { [K in keyof ProfileStyle]?: Choice<K> } = {
  font: Object.entries(FONTS).map(([key, font]) => [key as ProfileStyle['font'], font.label]),
  colours: [
    ['branch', 'Branch colours'],
    ['colour-blind', 'Colour-blind safe'],
    ['grey', 'Greys'],
    ['black', 'Black'],
  ],
  text: [
    ['soft', 'Soft greys'],
    ['black', 'Black'],
  ],
  connector: [
    ['straight', 'Straight'],
    ['curved', 'Curved'],
  ],
  dash: [
    ['solid', 'Solid'],
    ['dashed', 'Dashed'],
    ['dotted', 'Dotted'],
    ['by-pathway', 'A different dash per pathway'],
  ],
  value_position: [
    ['above', 'Above the level'],
    ['below', 'Below the level'],
    ['hidden', 'Hidden'],
  ],
  name_position: [
    ['below', 'Below the level'],
    ['above', 'Above the level'],
    ['hidden', 'Hidden'],
  ],
  brackets: [
    ['none', '12.3'],
    ['round', '(12.3)'],
    ['square', '[12.3]'],
  ],
  decimals: [
    ['unit', "The energy unit's own"],
    ['0', '0'],
    ['1', '1'],
    ['2', '2'],
    ['3', '3'],
  ],
  legend: [
    ['top-right', 'Top right'],
    ['top-left', 'Top left'],
    ['hidden', 'Hidden'],
  ],
  background: [
    ['white', 'White'],
    ['none', 'Transparent'],
  ],
}

/** D106: the energy profile's style, with a live preview of the profile in the drawer. Saved as
 * an app-wide setting, so every profile and every saved PNG or SVG follows it. */
export function ProfileStyleDialog({
  settings,
  preview,
  onSave,
  onClose,
}: {
  settings: Settings
  preview: { data: Profiles; colours: string[]; names: string[] } | null
  onSave: (style: ProfileStyle) => Promise<unknown>
  onClose: () => void
}) {
  const [style, setStyle] = useState<ProfileStyle>(settings.profile_style)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof ProfileStyle>(key: K, value: ProfileStyle[K]) =>
    setStyle((current) => ({ ...current, [key]: value }))

  const select = <K extends keyof ProfileStyle>(key: K, label: string) => (
    <label className="field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={String(style[key])}
        onChange={(event) => set(key, (CHOICES[key] as Choice<K>).find(([v]) => String(v) === event.target.value)![0])}
      >
        {(CHOICES[key] as Choice<K>).map(([value, text]) => (
          <option key={String(value)} value={String(value)}>
            {text}
          </option>
        ))}
      </select>
    </label>
  )
  const number = (key: keyof ProfileStyle, label: string, min: number, max: number, step = 1) => (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={style[key] as number}
        onChange={(event) => {
          const value = Number(event.target.value)
          if (event.target.value !== '' && Number.isFinite(value))
            set(key, Math.min(max, Math.max(min, value)) as never)
        }}
      />
    </label>
  )
  const flag = (key: 'title' | 'grid' | 'y_axis' | 'step_names', label: string) => (
    <label className="check">
      <input type="checkbox" checked={style[key]} onChange={(event) => set(key, event.target.checked)} />
      <span>{label}</span>
    </label>
  )
  const group = (title: string, children: ReactNode) => (
    <fieldset className="style-group">
      <legend>{title}</legend>
      {children}
    </fieldset>
  )

  const save = () =>
    onSave(style).then(onClose, (err: unknown) => setError(err instanceof Error ? err.message : String(err)))

  return (
    <Modal
      title="Profile style"
      wide
      onClose={onClose}
      actions={
        <>
          <button className="primary" onClick={save}>
            Save
          </button>
          <button onClick={onClose}>Cancel</button>
        </>
      }
    >
      <div className="style-presets" role="group" aria-label="Presets">
        <span className="muted small">Start from:</span>
        <button className="small" onClick={() => setStyle(settings.profile_presets.screen)}>
          Screen
        </button>
        <button className="small" onClick={() => setStyle(settings.profile_presets.publication)}>
          Publication
        </button>
        <span className="muted small">
          Every profile in the app, and every PNG and SVG saved from one, uses this style.
        </span>
      </div>
      <div className="style-preview" aria-label="Preview">
        {preview ? (
          <ProfileChart
            data={preview.data}
            colours={preview.colours}
            names={preview.names}
            settings={settings}
            style={style}
          />
        ) : (
          <p className="muted placeholder">Add a pathway in the drawer to see a preview here.</p>
        )}
      </div>
      <div className="style-groups">
        {group(
          'Figure',
          <>
            <div className="field-pair">
              {number('width', 'Width', 300, 4000, 10)}
              {number('height', 'Height', 150, 3000, 10)}
            </div>
            {select('background', 'Background')}
            {number('png_scale', 'PNG resolution (× the size)', 1, 8)}
          </>,
        )}
        {group(
          'Text',
          <>
            {select('font', 'Font')}
            {number('font_size', 'Font size', 6, 32, 0.5)}
            {select('text', 'Text colour')}
            {flag('title', 'Title')}
            {flag('y_axis', 'Energy axis')}
            {flag('step_names', 'Step names')}
          </>,
        )}
        {group(
          'Levels and connectors',
          <>
            {select('colours', 'Colours')}
            <div className="field-pair">
              {number('level_width', 'Level length', 6, 200)}
              {number('level_thickness', 'Level thickness', 0.5, 12, 0.5)}
            </div>
            {select('connector', 'Connectors')}
            {select('dash', 'Connector line')}
            {number('connector_width', 'Connector thickness', 0.25, 8, 0.25)}
          </>,
        )}
        {group(
          'Labels',
          <>
            {select('value_position', 'Energy value')}
            {select('name_position', 'Node name')}
            {select('brackets', 'Value written as')}
            {select('decimals', 'Decimals')}
            {select('legend', 'Legend')}
            {flag('grid', 'Grid lines')}
          </>,
        )}
      </div>
      {error && (
        <p role="alert" className="error small">
          {error}
        </p>
      )}
    </Modal>
  )
}
