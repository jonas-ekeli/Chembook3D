import { useEffect, useState } from 'react'
import { api, type LocalJob, type LocalTools, type Node } from '../api'
import { duration } from '../localJobs'
import { Modal } from './Modal'
import { errorText } from '../util'

/** How a single point's time grows with the atoms (local_jobs.SP_POWER) */
const SP_POWER = 2.7

/** D240: a single point of each structure as it is, run with Psi4 on this computer, one job
 * per node one after another; each comes back as a single-point calculation on its node, at the
 * composite level "level // the node's geometry level". */
export function SinglePointDialog({
  nodes,
  onClose,
  onStarted,
  onSettings,
}: {
  /** the nodes asked about; those without coordinates are left out */
  nodes: Node[]
  onClose: () => void
  onStarted: (jobs: LocalJob[]) => void
  /** Settings › Local calculations, where the tools are installed */
  onSettings?: () => void
}) {
  const structures = nodes.filter((n) => n.atom_count > 0)
  const skipped = nodes.length - structures.length
  const largest = Math.max(0, ...structures.map((n) => n.atom_count))
  const [tools, setTools] = useState<LocalTools | null>(null)
  const [level, setLevel] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.localTools(largest || undefined).then(
      (found) => !cancelled && setTools(found),
      (err: unknown) => !cancelled && setError(errorText(err)),
    )
    return () => {
      cancelled = true
    }
  }, [largest])

  const levels = tools?.single_point_levels ?? []
  const chosen = levels.find((l) => l.value === level) ?? levels.find((l) => l.default) ?? levels[0] ?? null
  // The estimate is for the largest structure; the others scale with their atoms.
  const secondsOf = (seconds: number, atoms: number) => seconds * (atoms / Math.max(largest, 1)) ** SP_POWER
  const total = chosen?.estimate ? structures.reduce((sum, n) => sum + secondsOf(chosen.estimate!.seconds, n.atom_count), 0) : null
  const installed = Boolean(tools?.installed)

  const start = () => {
    if (!chosen) return
    setBusy(true)
    setError(null)
    api.startSinglePoints({ node_ids: structures.map((n) => n.id), level: chosen.value }).then(
      (jobs) => {
        setBusy(false)
        onStarted(jobs)
        onClose()
      },
      (err: unknown) => {
        setBusy(false)
        setError(errorText(err))
      },
    )
  }

  const title = structures.length === 1 ? `Single point of “${structures[0].label}”` : `Single points of ${structures.length} structures`
  return (
    <Modal
      title={title}
      onClose={onClose}
      note={installed && tools ? `${tools.cores} core${tools.cores === 1 ? '' : 's'}, ${Math.round(tools.memory_mb / 100) / 10} GB on this computer` : undefined}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!installed || !chosen || structures.length === 0 || busy}
            onClick={start}
            title="Start on this computer (D240)"
          >
            {busy ? 'Starting…' : structures.length > 1 ? `Start ${structures.length}` : 'Start'}
          </button>
        </>
      }
    >
      {tools && !installed && (
        <p className="notice warn">
          The tools for local calculations are not installed yet.{' '}
          {onSettings ? (
            <button className="link" onClick={onSettings}>
              Install them in Settings
            </button>
          ) : (
            'Install them in Settings › Local calculations.'
          )}
        </p>
      )}
      <p>
        The energy of {structures.length === 1 ? 'the structure' : 'each structure'} as it is, with Psi4 on this computer. It is added to{' '}
        {structures.length === 1 ? 'the node' : 'each node'} as a single-point calculation whose geometry level is the node’s optimization, for
        example “Psi4 PBE-D3M(BJ)/def2-TZVP // MACE MACE-OMOL-0”. With a frequency job at that geometry level, free energies at this level follow.
        For ranking and checks before your own HPC runs.
      </p>
      <div className="fields">
        <label className="field">
          <span>Level</span>
          <select aria-label="Level" value={chosen?.value ?? ''} onChange={(event) => setLevel(event.target.value)}>
            {levels.map((l) => (
              <option key={l.value} value={l.value}>
                {l.estimate ? `${l.label} · about ${duration(Math.max(1, l.estimate.seconds))}` : l.label}
                {l.default ? ' (default)' : ''}
              </option>
            ))}
          </select>
          {chosen && (
            <small className="muted" aria-label="Level help">
              {chosen.note}
              {chosen.estimate &&
                ` About ${duration(Math.max(1, chosen.estimate.seconds))} for ${largest} atoms` +
                  (chosen.estimate.measured ? ', as measured on this computer.' : ' (a first guess until one has run here).')}
            </small>
          )}
        </label>
      </div>
      <ul className="plain small" aria-label="Structures">
        {structures.map((n) => (
          <li key={n.id}>
            {n.label || 'Unnamed'} · {n.atom_count} atoms · charge {n.charge ?? 0}, multiplicity {n.multiplicity ?? 1}
          </li>
        ))}
      </ul>
      {skipped > 0 && (
        <p className="muted small">
          {skipped} selected node{skipped === 1 ? ' has' : 's have'} no coordinates and {skipped === 1 ? 'is' : 'are'} left out.
        </p>
      )}
      {total !== null && structures.length > 1 && (
        <p className="muted small" aria-label="Estimate">
          About {duration(total)} in all, one after another.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}
