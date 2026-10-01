import { useEffect, useState } from 'react'
import { api, type Calculation, type Modes } from '../api'
import { withDisplacements } from '../util'

/** Pick a vibrational mode of one of the node's frequency calculations; imaginary modes are
 * listed first (FR-3D-03). */
export function Vibrations({
  nodeId,
  refreshKey,
  onChange,
}: {
  nodeId: string
  refreshKey: number
  onChange: (vibration: { xyz: string } | null) => void
}) {
  const [frequencyCalcs, setFrequencyCalcs] = useState<Calculation[]>([])
  const [calcId, setCalcId] = useState<string>('')
  const [modes, setModes] = useState<Modes | null>(null)
  const [mode, setMode] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.calculations(nodeId).then(
      (all) => {
        const found = all.filter((c) => c.type === 'frequency' && c.result && c.result.frequencies.length > 0)
        setFrequencyCalcs(found)
        setCalcId(found.length ? found[found.length - 1].id : '')
      },
      () => setFrequencyCalcs([]),
    )
  }, [nodeId, refreshKey])

  useEffect(() => {
    setModes(null)
    setMode('')
    onChange(null)
    if (!calcId) return
    api.modes(calcId).then(setModes, (err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    // onChange is a state setter from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calcId])

  if (frequencyCalcs.length === 0) return null
  return (
    <div className="vibrations" aria-label="Vibrations">
      {frequencyCalcs.length > 1 && (
        <select aria-label="Frequency calculation" value={calcId} onChange={(event) => setCalcId(event.target.value)}>
          {frequencyCalcs.map((c) => (
            <option key={c.id} value={c.id}>
              {c.composite_label}
            </option>
          ))}
        </select>
      )}
      <select
        aria-label="Animate mode"
        value={mode}
        disabled={!modes}
        onChange={(event) => {
          setMode(event.target.value)
          const index = event.target.value === '' ? -1 : Number(event.target.value)
          onChange(modes && index >= 0 ? { xyz: withDisplacements(modes.xyz, modes.modes[index]) } : null)
        }}
      >
        <option value="">No animation</option>
        {modes?.order.map((index) => (
          <option key={index} value={index}>
            Mode {index + 1}: {modes.frequencies[index] < 0 ? `${Math.abs(modes.frequencies[index]).toFixed(1)}i` : modes.frequencies[index].toFixed(1)} cm⁻¹
          </option>
        ))}
      </select>
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
