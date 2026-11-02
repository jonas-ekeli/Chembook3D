import { useEffect, useState } from 'react'
import { api, type LocalJob, type LocalTools, type Node } from '../api'
import { Modal } from './Modal'
import { errorText } from '../util'

/** What each method is for (D213); the server says which are offered. */
const HELP: Record<string, string> = {
  gfn2: 'Seconds to minutes. A quick clean-up before a DFT optimization on the cluster.',
  stub: 'A stand-in for tests.',
}

/** D213, FR-LOC-02: start a geometry optimization of a node on this computer. The result is
 * imported onto the node when it finishes (a node without calculations takes its geometry; on
 * one with calculations it goes to a derived node, D23). */
export function RefineDialog({
  node,
  onClose,
  onStarted,
  onSettings,
}: {
  node: Node
  onClose: () => void
  onStarted: (job: LocalJob) => void
  onSettings: () => void
}) {
  const [tools, setTools] = useState<LocalTools | null>(null)
  const [method, setMethod] = useState('gfn2')
  const [charge, setCharge] = useState(String(node.charge ?? 0))
  const [multiplicity, setMultiplicity] = useState(String(node.multiplicity ?? 1))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.localTools().then(setTools, (err: unknown) => setError(errorText(err)))
  }, [])

  const chargeValue = Number(charge)
  const multiplicityValue = Number(multiplicity)
  const valid =
    Number.isInteger(chargeValue) && charge.trim() !== '' && Number.isInteger(multiplicityValue) && multiplicityValue >= 1
  const ready = Boolean(tools?.installed) && node.atom_count > 0

  const start = () => {
    setBusy(true)
    setError(null)
    api
      .startRefine({ node_id: node.id, method, charge: chargeValue, multiplicity: multiplicityValue })
      .then(
        (job) => {
          onStarted(job)
          onClose()
        },
        (err: unknown) => setError(errorText(err)),
      )
      .finally(() => setBusy(false))
  }

  return (
    <Modal
      title={`Refine geometry of “${node.label}”`}
      onClose={onClose}
      note={tools?.installed ? `${tools.cores} core${tools.cores === 1 ? '' : 's'}, ${Math.round(tools.memory_mb / 100) / 10} GB on this computer` : undefined}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!ready || !valid || busy} onClick={start}>
            {busy ? 'Starting…' : 'Start'}
          </button>
        </>
      }
    >
      {tools && !tools.installed && (
        <p className="notice warn">
          The tools for local calculations are not installed yet.{' '}
          <button className="link" onClick={onSettings}>
            Install them in Settings
          </button>
        </p>
      )}
      {node.atom_count === 0 && <p className="notice warn">This node has no coordinates.</p>}
      <div className="fields">
        <label className="field">
          <span>Method</span>
          <select aria-label="Method" value={method} onChange={(event) => setMethod(event.target.value)}>
            {(tools?.methods ?? [{ value: 'gfn2', label: 'GFN2-xTB' }]).map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
          {HELP[method] && <small className="muted">{HELP[method]}</small>}
        </label>
        <div className="field-pair">
          <label className="field">
            <span>Charge</span>
            <input aria-label="Charge" type="number" value={charge} onChange={(event) => setCharge(event.target.value)} />
          </label>
          <label className="field">
            <span>Multiplicity</span>
            <input
              aria-label="Multiplicity"
              type="number"
              min={1}
              value={multiplicity}
              onChange={(event) => setMultiplicity(event.target.value)}
            />
          </label>
        </div>
        <p className="muted small">
          {node.atom_count} atoms. The job runs on this computer as a program of its own and goes on when the app is
          closed. Its result is imported onto this node when it is done
          {node.calculation_count > 0 ? '; since the node has calculations, it may go to a derived node' : ''}.
        </p>
      </div>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}
