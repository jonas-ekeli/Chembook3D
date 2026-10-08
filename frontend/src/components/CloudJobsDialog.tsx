import { useCallback, useEffect, useState } from 'react'
import { api, type CloudJob } from '../api'
import { statusText } from '../cloudJobs'
import { Modal } from './Modal'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function created(job: CloudJob): string {
  const when = new Date(job.created)
  return Number.isNaN(when.getTime()) ? job.created : when.toLocaleString()
}

/** D115: the investigation's cloud calculation jobs (D93) with where each stands, its session,
 * and what can be done with it: start it, check GitHub for its result, fetch the result, or
 * import a scan path (which the app also does by itself while the investigation is open). */
export function CloudJobsDialog({
  linked,
  onClose,
  onImported,
  onSelectNode,
}: {
  linked: boolean
  onClose: () => void
  onImported: (label: string, nodeId: string) => void
  onSelectNode: (id: string) => void
}) {
  const [jobs, setJobs] = useState<CloudJob[] | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const load = useCallback(
    (refresh: boolean) =>
      api.jobs(refresh).then(
        (list) => setJobs([...list].reverse()),
        (err: unknown) => setError(errorText(err)),
      ),
    [],
  )

  useEffect(() => {
    void load(false)
  }, [load])

  const act = (key: string, run: () => Promise<string | null>) => {
    setBusy(key)
    setError(null)
    setMessage(null)
    run()
      .then(
        (done) => setMessage(done),
        (err: unknown) => setError(errorText(err)),
      )
      .finally(() => {
        setBusy(null)
        void load(false)
      })
  }

  const importPath = (job: CloudJob, again: boolean) =>
    act(`import:${job.id}`, async () => {
      const result = await api.importPath(job.id, again)
      onImported(result.label, result.node_id)
      return `Imported the path as “${result.label}”.`
    })

  const actions = (job: CloudJob) => {
    const out = []
    const disabled = busy !== null
    if (job.status === 'draft' || job.status === 'launch_failed')
      out.push(
        <button key="start" disabled={disabled} onClick={() => act(`start:${job.id}`, () => api.startJob(job.id).then(() => null))}>
          Start
        </button>,
      )
    if (job.status === 'running' || job.status === 'starting')
      out.push(
        <button
          key="check"
          disabled={disabled}
          onClick={() =>
            act(`check:${job.id}`, async () => {
              const now = await api.job(job.id)
              return now.status === 'running' ? `“${job.name}” has no result on GitHub yet.` : null
            })
          }
        >
          {busy === `check:${job.id}` ? 'Checking…' : 'Check now'}
        </button>,
      )
    if (job.kind === 'scan_path') {
      const ready = job.status === 'finished' || job.status === 'fetched'
      if (job.imported?.present)
        out.push(
          <button
            key="show"
            onClick={() => {
              onSelectNode(job.imported!.node_id)
              onClose()
            }}
          >
            Show node
          </button>,
        )
      else if (job.imported)
        out.push(
          <button key="again" disabled={disabled} onClick={() => importPath(job, true)} title="Its node was removed">
            Import again
          </button>,
        )
      else if (ready)
        out.push(
          <button key="import" disabled={disabled} onClick={() => importPath(job, false)}>
            {job.import_error ? 'Try import again' : 'Import path'}
          </button>,
        )
    } else if (job.status === 'finished')
      out.push(
        <button
          key="fetch"
          disabled={disabled}
          onClick={() =>
            act(`fetch:${job.id}`, async () => {
              const fetched = await api.fetchJob(job.id)
              return `Copied ${fetched.files.length} file${fetched.files.length === 1 ? '' : 's'} into jobs/${job.id}/; import them from there.`
            })
          }
        >
          Fetch results
        </button>,
      )
    return out
  }

  const problems = (job: CloudJob) =>
    [job.launch_error && job.status === 'launch_failed' ? job.launch_error : null, job.import_error, job.warning].filter(
      (p): p is string => Boolean(p),
    )

  return (
    <Modal
      title="Cloud jobs"
      wide
      onClose={onClose}
      actions={
        <>
          <button disabled={busy !== null || !linked} onClick={() => act('all', () => load(true).then(() => null))}>
            {busy === 'all' ? 'Checking…' : 'Check all now'}
          </button>
          <button className="primary" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {!linked && (
        <p className="notice warn">
          This investigation is not synced with GitHub, so cloud sessions cannot return results to it (D93).
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {jobs && jobs.length === 0 && (
        <p className="muted">
          No cloud jobs yet. Ask Claude in the Claude panel to run an xTB or CREST calculation, or send a scan path
          from the side panel of two connected nodes.
        </p>
      )}
      {jobs && jobs.length > 0 && (
        <>
          <p className="muted small">
            While the investigation is open, the app looks on GitHub every few minutes and imports each finished scan
            path as a new node between its ends.
          </p>
          <table className="steps cloud-jobs" aria-label="Cloud jobs">
            <thead>
              <tr>
                <th>Job</th>
                <th>Status</th>
                <th>Session</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} aria-label={job.name}>
                  <td>
                    <strong>{job.name}</strong>
                    <div className="muted small">
                      {job.scan_path
                        ? `Scan path from “${job.scan_path.start_label}” to “${job.scan_path.end_label}”`
                        : 'Calculation'}
                      {' · '}
                      {created(job)}
                    </div>
                    {job.result?.summary && <div className="small">{job.result.summary}</div>}
                  </td>
                  <td>
                    {job.imported ? (job.imported.present ? 'Imported' : 'Imported, node removed') : statusText(job)}
                    {problems(job).map((p) => (
                      <div key={p} className="small cloud-job-problem">
                        {p}
                      </div>
                    ))}
                  </td>
                  <td>
                    {job.session_url ? (
                      <a href={job.session_url} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    ) : (
                      <span className="muted">–</span>
                    )}
                  </td>
                  <td className="cloud-job-actions">{actions(job)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Modal>
  )
}
