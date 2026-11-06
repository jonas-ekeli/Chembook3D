import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type LocalConstraints, type LocalJob, type LocalScan } from './api'
import { measure, parseAtomList, type Atom } from './chem'
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
      if (job.progress.phase) return `Running, ${job.progress.phase.toLowerCase()}`
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
const KCAL = 627.509474

export function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

/** The optimizer's progress: step, energy change since the start, largest gradient. */
export function progressText(job: LocalJob): string | null {
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

/** One held coordinate as typed in Refine geometry: its atoms (from 1) and a target, empty for
 * the current value. */
export type Held = { atoms: string; value: string }

/** D165: the coordinates an earlier constrained optimization on the node held ("constrained
 * B(1,2)", "frozen D(3,4,9,10)"), to offer them again. Frozen atoms are only counted there. */
export function heldBefore(constraints: string[]): Held[] {
  const found: Held[] = []
  for (const text of constraints) {
    const match = /\b[BAD]\((\d+(?:,\d+){1,3})\)/.exec(text)
    if (match && !/^scan\b|relaxed scan/i.test(text)) {
      const atoms = match[1].split(',').join(' ')
      if (!found.some((h) => h.atoms === atoms)) found.push({ atoms, value: '' })
    }
  }
  return found
}


const KINDS = { 2: 'distances', 3: 'angles', 4: 'dihedrals' } as const

/** The constraints of Refine geometry as the request wants them (atoms from 0), or what is wrong
 * with them; `count` is the structure's number of atoms. */
export function readConstraints(
  frozen: string,
  held: Held[],
  count: number,
): { value: LocalConstraints | null } | { error: string } {
  const out: Required<LocalConstraints> = { frozen: [], distances: [], angles: [], dihedrals: [] }
  const inRange = (n: number) => n >= 1 && n <= count
  if (frozen.trim()) {
    const read = parseAtomList(frozen)
    if ('error' in read) return { error: `Frozen atoms: ${read.error}` }
    if (!read.numbers.every(inRange)) return { error: `Frozen atoms are numbered 1 to ${count}` }
    out.frozen = read.numbers.map((n) => n - 1)
  }
  for (const item of held) {
    const read = parseAtomList(item.atoms)
    if ('error' in read) return { error: read.error }
    const n = read.numbers.length
    if (n < 2 || n > 4 || new Set(read.numbers).size !== n)
      return { error: `“${item.atoms}”: give 2, 3 or 4 different atoms` }
    if (!read.numbers.every(inRange)) return { error: `Atoms are numbered 1 to ${count}` }
    const target = item.value.trim() === '' ? null : Number(item.value)
    if (target !== null && !Number.isFinite(target)) return { error: `“${item.value}” is not a number` }
    out[KINDS[n as 2 | 3 | 4]].push({ atoms: read.numbers.map((a) => a - 1), value: target })
  }
  const any = out.frozen.length + out.distances.length + out.angles.length + out.dihedrals.length
  return { value: any ? out : null }
}

export type ScanRow = { atoms: string; start: string; end: string; points?: string }
export const MAX_SCAN_COORDINATES = 4
export const MAX_SCAN_POINTS = 100
export const MAX_GRID_SIDE = 30
export const MAX_GRID_POINTS = 400
/** D219: the methods a 2D grid runs with (cheap enough for hundreds of optimizations) */
export const GRID_METHODS = new Set(['gfn2', 'gxtb', 'stub'])
const SCAN_KINDS = { 2: 'distance', 3: 'angle', 4: 'dihedral' } as const

/** D218: the scan of Refine geometry as the request wants it (atoms from 0), or what is wrong
 * with it. An empty start is the coordinate's current value in `atoms`; all coordinates step
 * together over the same points (a concerted scan). */
export function readScan(
  rows: ScanRow[],
  points: string,
  atoms: Atom[],
  grid = false,
): { value: LocalScan } | { error: string } {
  const count = atoms.length
  const n = Number(points)
  if (!grid && (!Number.isInteger(n) || n < 2 || n > MAX_SCAN_POINTS))
    return { error: `A scan has 2 to ${MAX_SCAN_POINTS} points` }
  if (!rows.length) return { error: 'Add a coordinate to scan' }
  if (grid && rows.length !== 2) return { error: 'A grid scan has two coordinates' }
  const coordinates: LocalScan['coordinates'] = []
  for (const row of rows) {
    const read = parseAtomList(row.atoms)
    if ('error' in read) return { error: `Scan: ${read.error}` }
    const k = read.numbers.length
    if (k < 2 || k > 4 || new Set(read.numbers).size !== k)
      return { error: `Scan “${row.atoms}”: give 2, 3 or 4 different atoms` }
    if (!read.numbers.every((a) => a >= 1 && a <= count)) return { error: `Scan atoms are numbered 1 to ${count}` }
    const now = measure(read.numbers.map((a) => atoms[a - 1]))
    const start = row.start.trim() === '' ? (now?.value ?? NaN) : Number(row.start)
    const end = Number(row.end)
    if (!Number.isFinite(start)) return { error: `Scan from “${row.start}” is not a number` }
    if (row.end.trim() === '' || !Number.isFinite(end)) return { error: `Scan of ${row.atoms}: give the value to scan to` }
    if (Math.abs(end - start) < 1e-6) return { error: `Scan of ${row.atoms}: the start and end are the same` }
    const side = Number(row.points ?? '')
    if (grid && (!Number.isInteger(side) || side < 2 || side > MAX_GRID_SIDE))
      return { error: `Scan of ${row.atoms}: a grid coordinate has 2 to ${MAX_GRID_SIDE} points` }
    coordinates.push({
      kind: SCAN_KINDS[k as 2 | 3 | 4],
      atoms: read.numbers.map((a) => a - 1),
      start,
      end,
      ...(grid ? { points: side } : {}),
    })
  }
  if (!grid) return { value: { coordinates, points: n } }
  const total = (coordinates[0].points ?? 0) * (coordinates[1].points ?? 0)
  if (total > MAX_GRID_POINTS) return { error: `A grid has at most ${MAX_GRID_POINTS} points in all (${total} asked)` }
  return { value: { coordinates, points: total, grid: true } }
}

/** D218: a finished scan's points in a sentence: how many converged and where the energy tops. */
export function scanText(job: LocalJob): string | null {
  const points = job.result?.scan?.points
  if (!points?.length) return null
  const done = points.filter((p) => p.converged).length
  const energies = points.map((p) => p.energy)
  const known = energies.filter((e): e is number => e !== null)
  const head = `${done} of ${points.length} points converged`
  if (known.length < 2) return `${head}.`
  const span = (Math.max(...known) - Math.min(...known)) * 627.509474
  return `${head}; the energy spans ${span.toFixed(1)} kcal/mol.`
}

/** D215: what the check of a TS search's final structure found, in a sentence; null when the
 * job is no TS search or has no final check. */
export function tsCheckText(job: LocalJob): string | null {
  const check = job.result?.ts_check
  const end = check?.end
  if (!check || !end) return null
  const level = check.level ?? 'GFN2-xTB'
  const n = end.imaginary.length
  if (n === 0) return `${level} check: no imaginary frequency at the result, so it is not a transition state there.`
  const frequencies = end.imaginary.map((f) => `${Math.round(f)} cm⁻¹`).join(', ')
  const head = `${level} check: ${n === 1 ? 'one imaginary frequency' : `${n} imaginary frequencies`} (${frequencies})`
  if (end.on_reacting === undefined) {
    const atoms = (end.atoms ?? []).map((a) => a + 1).join(', ')
    return `${head}, moving atoms ${atoms}.`
  }
  return end.on_reacting ? `${head}, on the reacting atoms.` : `${head}, but its mode is mostly away from the reacting atoms.`
}
