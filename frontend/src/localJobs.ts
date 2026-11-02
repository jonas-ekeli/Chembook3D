import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type LocalJob } from './api'
import { errorText } from './util'

/** How often the open investigation reads its local jobs while one runs, and otherwise. */
export const ACTIVE_POLL = 3000
export const IDLE_POLL = 60_000
const EMPTY: LocalJob[] = []

export function localStatusText(job: LocalJob): string {
  switch (job.status) {
    case 'starting':
      return 'Starting'
    case 'queued':
      return 'Waiting for another local job'
    case 'running':
      return job.progress.cycle ? `Running, step ${job.progress.cycle}` : 'Running'
    case 'cancelling':
      return 'Stopping'
    case 'converged':
      return 'Converged'
    case 'not_converged':
      return 'Stopped before converging'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    case 'stopped':
      return 'Ended without a result'
  }
}

/** A finished result the app imports by itself: not imported yet, and not one whose import
 * failed (the user retries those from the jobs list). */
export function awaitingLocalImport(job: LocalJob): boolean {
  return job.importable && !job.import_error
}

/** D213: the open investigation's local jobs, read every few seconds while one is starting,
 * waiting or running, and each finished result imported onto its node by itself. */
export function useLocalJobs(
  folder: string | null,
  onImported: (job: LocalJob) => void,
  onFailed: (job: LocalJob, message: string) => void,
): { jobs: LocalJob[]; refresh: () => void } {
  // The jobs as last read, with the folder they belong to: another folder's are never shown.
  const [read, setRead] = useState<{ folder: string; jobs: LocalJob[] } | null>(null)
  const jobs = read && read.folder === folder ? read.jobs : EMPTY
  const imported = useRef(onImported)
  const failed = useRef(onFailed)
  useEffect(() => {
    imported.current = onImported
    failed.current = onFailed
  }, [onImported, onFailed])
  const checking = useRef(false)
  const [tick, setTick] = useState(0)

  const check = useCallback(async (from: string, stopped: () => boolean) => {
    if (checking.current) return
    checking.current = true
    try {
      let list = await api.localJobs()
      for (const job of list.filter(awaitingLocalImport)) {
        if (stopped()) return
        try {
          const done = await api.importLocalJob(job.id)
          if (!stopped()) imported.current(done)
        } catch (err) {
          const message = errorText(err)
          // Another tab imported it a moment ago: nothing to say.
          if (!stopped() && !message.includes('imported already')) failed.current(job, message)
        }
      }
      if (list.some(awaitingLocalImport)) list = await api.localJobs()
      if (!stopped()) setRead({ folder: from, jobs: list })
    } catch {
      // The backend restarting: the next check tries again.
    } finally {
      checking.current = false
    }
  }, [])

  const active = jobs.some((job) => job.active)
  useEffect(() => {
    if (!folder) return
    let stopped = false
    const first = window.setTimeout(() => void check(folder, () => stopped), 0)
    const timer = window.setInterval(() => void check(folder, () => stopped), active ? ACTIVE_POLL : IDLE_POLL)
    return () => {
      stopped = true
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [folder, active, tick, check])

  const refresh = useCallback(() => setTick((n) => n + 1), [])
  return { jobs, refresh }
}
