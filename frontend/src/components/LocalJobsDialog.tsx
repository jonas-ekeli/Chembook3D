import { Fragment, useEffect, useState } from 'react'
import { api, type LocalJob } from '../api'
import { localStatusText, scanText, tsCheckText } from '../localJobs'
import { Modal } from './Modal'
import { errorText, withDisplacements } from '../util'
import { Viewer3D } from './Viewer3D'

const KCAL = 627.509474

function created(job: LocalJob): string {
  const when = new Date(job.created)
  return Number.isNaN(when.getTime()) ? job.created : when.toLocaleString()
}

function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

/** The optimizer's progress: step, energy change since the start, largest gradient. */
function progressText(job: LocalJob): string | null {
  const { energies, max_gradient: gradient } = job.progress
  if (!energies.length) return null
  const parts = []
  if (energies.length > 1) parts.push(`ΔE ${((energies[energies.length - 1] - energies[0]) * KCAL).toFixed(2)} kcal/mol`)
  if (gradient !== null) parts.push(`max gradient ${gradient.toExponential(1)} Eh/bohr`)
  if (job.result?.elapsed_s) parts.push(duration(job.result.elapsed_s))
  else if (job.progress.started_at && job.active) {
    const started = new Date(job.progress.started_at).getTime()
    if (!Number.isNaN(started)) parts.push(`${duration((Date.now() - started) / 1000)} so far`)
  }
  return parts.join(' · ')
}

/** D213: the investigation's calculations on this computer, newest first, with their progress;
 * a waiting or running one can be cancelled, and a finished one's result is imported onto its
 * node by the app (or here, when that failed). */
export function LocalJobsDialog({
  jobs,
  onChanged,
  onClose,
  onImported,
  onSelectNode,
  onSettings,
}: {
  jobs: LocalJob[]
  onChanged: () => void
  onClose: () => void
  onImported: (job: LocalJob) => void
  onSelectNode: (id: string) => void
  onSettings: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showMode, setShowMode] = useState<string | null>(null) // D217: the job whose mode is shown
  // Read the jobs again on opening: one Claude started is not known yet.
  useEffect(() => onChanged(), [onChanged])

  const act = (key: string, run: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    run()
      .catch((err: unknown) => setError(errorText(err)))
      .finally(() => {
        setBusy(null)
        onChanged()
      })
  }

  const actions = (job: LocalJob) => {
    const disabled = busy !== null
    if (job.active)
      return (
        <button disabled={disabled || job.status === 'cancelling'} onClick={() => act(`cancel:${job.id}`, () => api.cancelLocalJob(job.id))}>
          {busy === `cancel:${job.id}` ? 'Stopping…' : 'Cancel'}
        </button>
      )
    if (job.imported?.present)
      return (
        <button
          onClick={() => {
            onSelectNode(job.imported!.node_id)
            onClose()
          }}
        >
          Show node
        </button>
      )
    if (job.importable)
      return (
        <button
          disabled={disabled}
          onClick={() =>
            act(`import:${job.id}`, async () => {
              onImported(await api.importLocalJob(job.id))
            })
          }
        >
          {job.import_error ? 'Try import again' : 'Import'}
        </button>
      )
    return null
  }

  return (
    <Modal
      title="Local jobs"
      wide
      onClose={onClose}
      actions={
        <>
          <button onClick={onSettings}>Local calculation settings…</button>
          <button className="primary" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {error && <p role="alert">{error}</p>}
      {jobs.length === 0 ? (
        <p className="muted">
          No local jobs yet. Choose Refine geometry… on a node to optimize it on this computer.
        </p>
      ) : (
        <>
          <p className="muted small">
            Each job runs as a program of its own, so it goes on when the app is closed; jobs run one after another.
            The app imports each result onto its node when it is done.
          </p>
          <table className="steps cloud-jobs" aria-label="Local jobs">
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const progress = progressText(job)
                const problem = job.result?.error || job.import_error
                const check = tsCheckText(job)
                const mode = job.result?.ts_check?.end?.mode
                const xyz = job.result?.final_xyz
                const shown = showMode === job.id && mode && xyz ? withDisplacements(xyz, mode) : null
                const found = job.result?.frequencies
                const scanned = scanText(job)
                return (
                  <Fragment key={job.id}>
                    <tr aria-label={job.id}>
                      <td>
                        <strong>{job.node_label}</strong>
                        <div className="muted small">
                          {job.level} · charge {job.charge}, multiplicity {job.multiplicity} · {job.atoms} atoms ·{' '}
                          {job.cores} core{job.cores === 1 ? '' : 's'} · {created(job)}
                          {job.constraints && ' · with constraints'}
                          {job.transition && ' · TS search'}
                          {job.scan &&
                            (job.scan.grid
                              ? ` · 2D grid scan, ${job.scan.points} points`
                              : ` · scan of ${job.scan.coordinates.length} coordinate${job.scan.coordinates.length === 1 ? '' : 's'}, ${job.scan.points} points`)}
                        </div>
                      </td>
                      <td>
                        {job.imported
                          ? job.imported.present
                            ? `${localStatusText(job)}, imported${job.imported.derived ? ' onto a derived node' : ''}`
                            : `${localStatusText(job)}, imported; node removed`
                          : localStatusText(job)}
                        {progress && <div className="small">{progress}</div>}
                        {check && (
                          <div className="small" aria-label="TS check">
                            {check}
                            {mode && xyz && (
                              <>
                                {' '}
                                <button
                                  className="small"
                                  aria-pressed={showMode === job.id}
                                  onClick={() => setShowMode((current) => (current === job.id ? null : job.id))}
                                >
                                  {showMode === job.id ? 'Hide mode' : 'Show mode'}
                                </button>
                              </>
                            )}
                          </div>
                        )}
                        {found && (
                          <div className="small muted" aria-label="Frequencies">
                            {found.level} frequencies at the result (
                            {found.imaginary.length ? `${found.imaginary.length} imaginary` : 'none imaginary'}), imported as a
                            frequency calculation.
                          </div>
                        )}
                        {scanned && (
                          <div className="small" aria-label="Scan">
                            {scanned}
                          </div>
                        )}
                        {problem && <div className="small cloud-job-problem">{problem}</div>}
                      </td>
                      <td className="cloud-job-actions">{actions(job)}</td>
                    </tr>
                    {shown && (
                      <tr>
                        <td colSpan={3}>
                          <div className="local-job-mode" aria-label="Imaginary mode">
                            <Viewer3D models={[{ xyz: xyz ?? '' }]} vibration={{ xyz: shown }} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </>
      )}
    </Modal>
  )
}
