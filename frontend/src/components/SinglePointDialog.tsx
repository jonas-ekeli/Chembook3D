import { useEffect, useState } from 'react'
import { api, type LocalJob, type LocalTools, type Node, type NodeGeometry } from '../api'
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
  // D246: a node can hold geometries at several levels; one node's single point picks one.
  const only = structures.length === 1 ? structures[0].id : null
  const [geometries, setGeometries] = useState<NodeGeometry[]>([])
  const [at, setAt] = useState<string | null>(null)

  useEffect(() => {
    if (!only) return
    let cancelled = false
    api.nodeGeometries(only).then(
      (found) => !cancelled && setGeometries(found),
      () => !cancelled && setGeometries([]),
    )
    return () => {
      cancelled = true
    }
  }, [only])

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
    const other = geometries.find((g) => g.calculation_id === at && !g.node_geometry)
    const geometry_of = only && other ? { [only]: other.calculation_id } : undefined
    api.startSinglePoints({ node_ids: structures.map((n) => n.id), level: chosen.value, geometry_of }).then(
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
        {geometries.length > 1 && (
          <label className="field">
            <span>At the geometry of</span>
            <select aria-label="At the geometry of" value={at ?? geometries[0].calculation_id} onChange={(event) => setAt(event.target.value)}>
              {geometries.map((g) => (
                <option key={g.calculation_id} value={g.calculation_id}>
                  {g.level_label ?? 'Unknown level'}
                  {g.node_geometry ? ' (the node’s geometry)' : g.pre_optimization ? ' (pre-optimization)' : ''}
                </option>
              ))}
            </select>
            <small className="muted">The optimization whose structure the single point is run at; its level becomes the geometry level (D246).</small>
          </label>
        )}
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
