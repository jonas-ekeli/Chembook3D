import { useState } from 'react'

// Small pieces the analyses in the Analyses view share (D83, D86, D171).

/** A number that saves when it loses focus; empty means null. */
export function NumberInput({
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
