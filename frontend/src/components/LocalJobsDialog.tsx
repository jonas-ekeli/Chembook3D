import { Fragment, useEffect, useState } from 'react'
import { api, type LocalJob } from '../api'
import { fixModeText, localStatusText, progressText, scanText, tsCheckText, tsGuessText } from '../localJobs'
import { Modal } from './Modal'
import { errorText, withDisplacements } from '../util'
import { Viewer3D } from './Viewer3D'

function created(job: LocalJob): string {
  const when = new Date(job.created)
  return Number.isNaN(when.getTime()) ? job.created : when.toLocaleString()
}

/** D213: the investigation's calculations on this computer, newest first, with their progress;
 * a waiting or running one can be cancelled, and a finished one's result is imported onto its
 * node by the app (or here, when that failed). D220: Inspect opens a job's steps, also while it
 * runs, in the Inspect window. */
export function LocalJobsDialog({
  jobs,
  onChanged,
  onClose,
  onImported,
  onSelectNode,
  onSettings,
  onInspect,
}: {
  jobs: LocalJob[]
  onChanged: () => void
  onClose: () => void
  onImported: (job: LocalJob) => void
  onSelectNode: (id: string) => void
  onSettings: () => void
  /** D220: open the job in the Inspect window */
  onInspect: (job: LocalJob) => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showMode, setShowMode] = useState<string | null>(null) // D217: the job whose mode is shown
  const [replacing, setReplacing] = useState<LocalJob | null>(null) // D260: the Replace being confirmed
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
    if (job.awaiting_choice)
      return (
        <>
          <button disabled={disabled} onClick={() => setReplacing(job)}>
            Replace…
          </button>
          <button
            disabled={disabled}
            onClick={() =>
              act(`import:${job.id}`, async () => {
                onImported(await api.importLocalJob(job.id, true))
              })
            }
          >
            Derived node
          </button>
        </>
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
                const fixed = fixModeText(job)
                const guess = tsGuessText(job)
                return (
                  <Fragment key={job.id}>
                    <tr aria-label={job.id}>
                      <td>
                        <strong>
                          {job.kind === 'ts_guess' && job.ts_guess
                            ? `TS guess “${job.ts_guess.start_label}” to “${job.ts_guess.end_label}”`
                            : job.node_label}
                        </strong>
                        <div className="muted small">
                          {job.level} · charge {job.charge}, multiplicity {job.multiplicity} · {job.atoms} atoms ·{' '}
                          {job.cores} core{job.cores === 1 ? '' : 's'} · {created(job)}
                          {job.start &&
                            ` · from ${job.start.from === 'geometry' ? `its ${job.start.label ?? 'other'} geometry` : 'a structure'}`}
                          {job.constraints && ' · with constraints'}
                          {job.transition && job.kind !== 'ts_guess' && ' · TS search'}
                          {job.fix_mode && ` · fix the ${Math.abs(job.fix_mode.wavenumber).toFixed(0)}i cm⁻¹ mode`}
                          {job.kind === 'single_point' && ' · single point'}
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
                        {guess.summary && (
                          <div className="small" aria-label="TS guess">
                            {guess.summary}
                          </div>
                        )}
                        {guess.warnings.map((w) => (
                          <div key={w} className="small cloud-job-problem">
                            {w}
                          </div>
                        ))}
                        {fixed && (
                          <div className="small" aria-label="Fix imaginary mode">
                            {fixed}
                          </div>
                        )}
                        {scanned && (
                          <div className="small" aria-label="Scan">
                            {scanned}
                          </div>
                        )}
                        {job.awaiting_choice && job.replace_offer && (
                          <div className="small" aria-label="Replace or derive">
                            The result ends elsewhere at {job.replace_offer.level_label}, which “{job.node_label}” already has:
                            replace that geometry, or import it onto a derived node.
                          </div>
                        )}
                        {problem && <div className="small cloud-job-problem">{problem}</div>}
                      </td>
                      <td className="cloud-job-actions">
                        {job.status !== 'starting' && job.status !== 'queued' && (
                          <button
                            onClick={() => onInspect(job)}
                            title={
                              job.active
                                ? 'Follow the optimization as it runs: each step in 3D, its energy and convergence criteria'
                                : 'Each step of the optimization in 3D, its energy and convergence criteria'
                            }
                          >
                            Inspect
                          </button>
                        )}
                        {actions(job)}
                      </td>
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
      {replacing?.replace_offer && (
        <ReplaceConfirm
          job={replacing}
          onCancel={() => setReplacing(null)}
          onReplace={() => {
            const job = replacing
            setReplacing(null)
            act(`replace:${job.id}`, async () => {
              onImported(await api.replaceWithLocalJob(job.id))
            })
          }}
        />
      )}
    </Modal>
  )
}

/** D260: what replacing the node's geometry at the job's level removes, for the user's confirm. */
function ReplaceConfirm({ job, onCancel, onReplace }: { job: LocalJob; onCancel: () => void; onReplace: () => void }) {
  const offer = job.replace_offer!
  return (
    <Modal
      title={`Replace the ${offer.level_label} geometry?`}
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="danger" disabled={offer.blockers.length > 0} onClick={onReplace}>
            Replace
          </button>
        </>
      }
    >
      <p>The result of this job takes the place of these on “{job.node_label}”, which are removed for good:</p>
      <ul aria-label="Calculations replaced">
        {offer.removes.map((c) => (
          <li key={c.id}>
            the {c.type.replace('_', ' ')} at {c.level ?? 'an unknown level'}
            {c.file && <span className="muted"> ({c.file})</span>}
          </li>
        ))}
      </ul>
      {offer.node_geometry && <p>The node’s coordinates move to the result.</p>}
      {offer.blockers.map((b) => (
        <p key={b} role="alert">
          {b}
        </p>
      ))}
    </Modal>
  )
}
