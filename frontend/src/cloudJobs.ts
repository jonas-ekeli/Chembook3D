import { useEffect, useRef } from 'react'
import { api, type CloudJob } from './api'

/** D115: how often an open investigation looks on GitHub for scan paths that came back. */
export const CHECK_EVERY = 150_000

/** A scan path whose result the app should import by itself: not imported yet, and not one
 * whose import failed (the user retries those from the jobs list). */
export function awaitingImport(job: CloudJob): boolean {
  return job.kind === 'scan_path' && !job.imported && !job.import_error && job.status !== 'draft'
}

export function statusText(job: CloudJob): string {
  switch (job.status) {
    case 'draft':
      return 'Not started'
    case 'starting':
      return 'Starting'
    case 'waiting_for_answer':
      return 'Waiting for an answer in the Claude panel'
    case 'launch_failed':
      return 'Could not start'
    case 'running':
      return 'Running'
    case 'finished':
      return 'Result on GitHub'
    case 'fetched':
      return 'Result fetched'
  }
}

/** D115: while an investigation is open, checks GitHub now and then for scan path jobs that
 * finished and imports each path as a node. Only scan paths in flight cost a fetch. */
export function useScanPathImports(
  folder: string | null,
  linked: boolean,
  onImported: (label: string, nodeId: string, onEdge: boolean) => void,
  onFailed: (job: CloudJob, message: string) => void,
) {
  const imported = useRef(onImported)
  const failed = useRef(onFailed)
  useEffect(() => {
    imported.current = onImported
    failed.current = onFailed
  }, [onImported, onFailed])

  useEffect(() => {
    if (!folder || !linked) return
    let stopped = false
    const check = async () => {
      try {
        if (!(await api.jobs(false)).some(awaitingImport)) return
        for (const job of await api.jobs(true)) {
          if (stopped) return
          if (!awaitingImport(job) || (job.status !== 'finished' && job.status !== 'fetched')) continue
          try {
            const result = await api.importPath(job.id)
            if (!stopped) imported.current(result.label, result.node_id, result.on_edge)
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err)
            // Another tab imported it a moment ago: nothing to say.
            if (!stopped && !message.includes('imported already')) failed.current(job, message)
          }
        }
      } catch {
        // Offline or the backend restarting: the next check tries again.
      }
    }
    void check()
    const timer = window.setInterval(() => void check(), CHECK_EVERY)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [folder, linked])
}
