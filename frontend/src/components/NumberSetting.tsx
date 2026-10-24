import { useState } from 'react'

/** A number field in the Settings dialog, saved when it loses focus with a valid new value.
 * D183: the number is right-aligned with its unit inside the field; `label` stays its
 * accessible name (with the unit, as "Temperature (K)"), the row's name being beside it. */
export function NumberSetting({
  label,
  value,
  onSave,
  unit,
  allowZero = false,
}: {
  label: string
  value: number
  onSave: (value: number) => void
  unit?: string
  allowZero?: boolean
}) {
  const [draft, setDraft] = useState(String(value))
  const parsed = Number(draft)
  const valid = draft.trim() !== '' && Number.isFinite(parsed) && (allowZero ? parsed >= 0 : parsed > 0)
  return (
    <span className={unit ? 'number-setting with-unit' : 'number-setting'}>
      <input
        aria-label={label}
        inputMode="decimal"
        value={draft}
        aria-invalid={!valid}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => valid && parsed !== value && onSave(parsed)}
      />
      {unit && (
        <span className="unit" aria-hidden="true">
          {unit}
        </span>
      )}
    </span>
  )
}
