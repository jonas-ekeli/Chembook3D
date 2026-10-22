import { useState } from 'react'

/** A number field in the Settings dialog, saved when it loses focus with a valid new value. */
export function NumberSetting({
  label,
  value,
  onSave,
  allowZero = false,
}: {
  label: string
  value: number
  onSave: (value: number) => void
  allowZero?: boolean
}) {
  const [draft, setDraft] = useState(String(value))
  const parsed = Number(draft)
  const valid = draft.trim() !== '' && Number.isFinite(parsed) && (allowZero ? parsed >= 0 : parsed > 0)
  return (
    <label className="field">
      <span>{label}</span>
      <input
        aria-label={label}
        inputMode="decimal"
        value={draft}
        aria-invalid={!valid}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => valid && parsed !== value && onSave(parsed)}
      />
    </label>
  )
}
