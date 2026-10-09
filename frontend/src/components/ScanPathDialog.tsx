import { useEffect, useMemo, useState } from 'react'
import { api, type CloudJob, type ScanPathPlan, type ScanPathTsEnd } from '../api'
import { describeMatch } from '../atomMatch'
import { measure, parseXyz } from '../chem'
import { AtomMatchReview } from './AtomMatchDialog'
import { Modal } from './Modal'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

type Held = { end: 'start' | 'end'; atoms: number[]; ticked: boolean; why: string; start_value: number; end_value: number; kind: string }

/** D119: one of the user's own coordinates to drive, from and to as typed. */
type Drive = { atoms: number[]; kind: string; start_value: number; end_value: number; from: string; to: string }

const wrap = (degrees: number) => ((((degrees + 180) % 360) + 360) % 360) - 180
/** Where a coordinate ends: a dihedral the short way round from its start value. */
const shortTo = (kind: string, from: number, end: number) => (kind === 'dihedral' ? from + wrap(end - from) : end)
const fixed = (kind: string, value: number) => value.toFixed(kind === 'distance' ? 3 : 1)
const sameAtoms = (a: number[], b: number[]) => a.join('-') === b.join('-') || a.join('-') === [...b].reverse().join('-')
const number = (text: string) => (text.trim() === '' ? Number.NaN : Number(text))

const PAIRS_KEY = 'chembook3d.scanPathPairs'

/** D114: hand-fixed atom pairs are offered again the next time the same two nodes are
 * scanned; kept in this browser only. */
function savedPairs(start: string, end: string): [number, number][] {
  try {
    const all = JSON.parse(localStorage.getItem(PAIRS_KEY) ?? '{}') as Record<string, [number, number][]>
    return all[`${start}:${end}`] ?? []
  } catch {
    return []
  }
}

function savePairs(start: string, end: string, pairs: [number, number][]) {
  try {
    const all = JSON.parse(localStorage.getItem(PAIRS_KEY) ?? '{}') as Record<string, [number, number][]>
    if (pairs.length) all[`${start}:${end}`] = pairs
    else delete all[`${start}:${end}`]
    localStorage.setItem(PAIRS_KEY, JSON.stringify(all))
  } catch {
    // storage may be unavailable; the pairs then last as long as the dialog
  }
}

const unit = (kind: string) => (kind === 'distance' ? 'Å' : '°')
const format = (kind: string, value: number) => `${value.toFixed(kind === 'distance' ? 3 : 1)} ${unit(kind)}`

function tsHeading(ts: ScanPathTsEnd): string {
  if (ts.guess) {
    return `“${ts.label}” is a TS guess with no imaginary mode to read: tick the coordinates that make it a TS.`
  }
  return `“${ts.label}” is a TS (imaginary mode ${Math.abs(ts.imaginary ?? 0).toFixed(0)}i cm⁻¹): these are held at its values.`
}

/** D114, A60: a scan path between two nodes joined by an edge, run by a Claude Code cloud
 * session (D93). The dialog shows the atom match (D113, reviewed when doubtful), the
 * coordinates held at a TS end, the user's own coordinates to drive (D119) and the solvent,
 * and sends the job. */
export function ScanPathDialog({ startId, endId, onClose }: { startId: string; endId: string; onClose: () => void }) {
  const [ends, setEnds] = useState({ start: startId, end: endId })
  const [members, setMembers] = useState<{ start: string | null; end: string | null }>({ start: null, end: null })
  const [pairs, setPairs] = useState<[number, number][] | null>(null)
  const [answer, setAnswer] = useState<{ key: string; plan: ScanPathPlan | null; error: string | null } | null>(null)
  const [held, setHeld] = useState<Held[]>([])
  const [reviewing, setReviewing] = useState<boolean | null>(null)
  const [solvent, setSolvent] = useState<string | null | undefined>(undefined)
  const [typed, setTyped] = useState<{ end: 'start' | 'end'; text: string }>({ end: 'end', text: '' })
  const [typedError, setTypedError] = useState<string | null>(null)
  const [drive, setDrive] = useState<Drive[]>([])
  const [driveOrder, setDriveOrder] = useState<'together' | 'staged'>('together')
  const [driveText, setDriveText] = useState('')
  const [driveError, setDriveError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sent, setSent] = useState<{
    job: CloudJob
    start_error: string | null
  } | null>(null)
  const [sendError, setSendError] = useState<string | null>(null)

  const key = JSON.stringify([ends, members, pairs])
  const plan = answer?.plan ?? null
  const busy = answer?.key !== key

  useEffect(() => {
    let cancelled = false
    api
      .scanPathPlan({
        start_id: ends.start,
        end_id: ends.end,
        pairs: pairs ?? undefined,
        start_member_id: members.start,
        end_member_id: members.end,
      })
      .then(
        (result) => {
          if (cancelled) return
          // The first plan of two nodes brings back the pairs fixed the last time.
          const remembered = pairs === null ? savedPairs(result.start.node_id, result.end.node_id) : []
          if (remembered.length) {
            setPairs(remembered)
            return
          }
          setAnswer({ key, plan: result, error: null })
          setHeld(result.ts_ends.flatMap((ts) => ts.suggested.map((s) => ({ ...s, end: ts.end }))))
          // A fixed pair moves the end's atoms: the rows keep their atoms, and a value still at
          // its old default follows the new match.
          const a = parseXyz(result.match.start_xyz)
          const b = parseXyz(result.match.renumbered_xyz)
          setDrive((rows) =>
            rows.flatMap((row) => {
              const start = measure(row.atoms.map((n) => a[n - 1]))
              const end = measure(row.atoms.map((n) => b[n - 1]))
              if (!start || !end) return []
              const oldTo = fixed(row.kind, shortTo(row.kind, row.start_value, row.end_value))
              return [
                {
                  ...row,
                  start_value: start.value,
                  end_value: end.value,
                  from: row.from === fixed(row.kind, row.start_value) ? fixed(row.kind, start.value) : row.from,
                  to: row.to === oldTo ? fixed(row.kind, shortTo(row.kind, start.value, end.value)) : row.to,
                },
              ]
            }),
          )
        },
        (err: unknown) => !cancelled && setAnswer({ key, plan: null, error: errorText(err) }),
      )
    return () => {
      cancelled = true
    }
    // `key` stands for the ends, members and pairs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const startAtoms = useMemo(() => (plan ? parseXyz(plan.match.start_xyz) : []), [plan])
  const endAtoms = useMemo(() => (plan ? parseXyz(plan.match.renumbered_xyz) : []), [plan])
  const review = reviewing ?? (plan ? !plan.match.confident : false)
  const chosenSolvent = solvent === undefined ? (plan?.solvent ?? null) : solvent
  const missing = plan?.ts_ends.filter((ts) => ts.guess && !held.some((h) => h.end === ts.end && h.ticked)) ?? []
  // The end a typed coordinate is held at: the one picked, if it is a TS, else the first TS end
  // (the TS may be the start, and swapping the ends changes which one it is).
  const typedEnd = plan?.ts_ends.some((ts) => ts.end === typed.end) ? typed.end : (plan?.ts_ends[0]?.end ?? typed.end)

  const fix = (next: [number, number][]) => {
    if (plan) savePairs(plan.start.node_id, plan.end.node_id, next)
    setPairs(next)
  }

  const swap = () => {
    setEnds({ start: ends.end, end: ends.start })
    setMembers({ start: members.end, end: members.start })
    setPairs(null)
    setReviewing(null)
    setDrive([]) // numbered in the start's atoms, which are now the other end's
  }

  const atomsOf = (text: string): number[] | null => {
    const atoms = text
      .split(/[\s,–-]+/)
      .filter(Boolean)
      .map((t) => Number.parseInt(t, 10))
    if (atoms.length < 2 || atoms.length > 4 || atoms.some((a) => !(a >= 1 && a <= startAtoms.length)) || new Set(atoms).size !== atoms.length) {
      return null
    }
    return atoms
  }

  const addTyped = () => {
    const atoms = atomsOf(typed.text)
    if (!atoms) {
      setTypedError(`Give 2 to 4 different atom numbers from 1 to ${startAtoms.length}.`)
      return
    }
    const a = measure(atoms.map((n) => startAtoms[n - 1]))
    const b = measure(atoms.map((n) => endAtoms[n - 1]))
    if (!a || !b) return
    // A coordinate already listed (in either direction) is ticked rather than listed twice.
    const same = (h: Held) => h.end === typedEnd && [atoms, [...atoms].reverse()].some((order) => order.join('-') === h.atoms.join('-'))
    if (held.some(same)) {
      setHeld(held.map((h) => (same(h) ? { ...h, ticked: true } : h)))
    } else {
      setHeld([...held, { end: typedEnd, atoms, ticked: true, why: 'added by hand', kind: a.kind, start_value: a.value, end_value: b.value }])
    }
    setTyped({ ...typed, text: '' })
    setTypedError(null)
  }

  const heldDriven = held.filter((h) => h.ticked)

  const addDrive = () => {
    const atoms = atomsOf(driveText)
    if (!atoms) {
      setDriveError(`Give 2 to 4 different atom numbers from 1 to ${startAtoms.length}.`)
      return
    }
    const a = measure(atoms.map((n) => startAtoms[n - 1]))
    const b = measure(atoms.map((n) => endAtoms[n - 1]))
    if (!a || !b) return
    const name = `${a.kind} ${atoms.join('–')}`
    if (heldDriven.some((h) => sameAtoms(h.atoms, atoms))) {
      setDriveError(`${name} is held at the TS, so it is driven to the other end's value already.`)
      return
    }
    if (drive.some((d) => sameAtoms(d.atoms, atoms))) {
      setDriveError(`${name} is listed already.`)
      return
    }
    const row = {
      atoms,
      kind: a.kind,
      start_value: a.value,
      end_value: b.value,
      from: fixed(a.kind, a.value),
      to: fixed(a.kind, shortTo(a.kind, a.value, b.value)),
    }
    setDrive([...drive, row])
    setDriveText('')
    setDriveError(null)
  }

  const changeDrive = (index: number, change: Partial<Drive>) => setDrive(drive.map((d, i) => (i === index ? { ...d, ...change } : d)))
  const driveBad = drive.find((d) => !Number.isFinite(number(d.from)) || !Number.isFinite(number(d.to)))

  const send = () => {
    if (!plan) return
    setSending(true)
    setSendError(null)
    api
      .sendScanPath({
        start_id: ends.start,
        end_id: ends.end,
        pairs: pairs ?? [],
        start_member_id: members.start,
        end_member_id: members.end,
        held: held.filter((h) => h.ticked).map((h) => ({ end: h.end, atoms: h.atoms })),
        drive: drive.map((d) => ({
          atoms: d.atoms,
          from: number(d.from),
          to: number(d.to),
        })),
        drive_order: driveOrder,
        solvent: chosenSolvent,
      })
      .then(
        (result) => {
          setSending(false)
          setSent(result)
        },
        (err: unknown) => {
          setSending(false)
          setSendError(errorText(err))
        },
      )
  }

  if (sent) {
    const { job, start_error } = sent
    return (
      <Modal title="Scan path" onClose={onClose} actions={<button className="primary" onClick={onClose}>Close</button>}>
        {start_error ? (
          <>
            <p role="alert">The job “{job.name}” is saved but could not be started: {start_error}</p>
            <p className="muted small">It can be started again from Cloud jobs once that is fixed.</p>
          </>
        ) : (
          <div aria-label="Scan path sent">
            <p>
              Sent “{job.name}” to a Claude Code cloud session.{' '}
              {job.session_url && (
                <a href={job.session_url} target="_blank" rel="noreferrer">
                  Open the session
                </a>
              )}
            </p>
            {job.status === 'waiting_for_answer' || job.status === 'starting' ? (
              <p className="muted small">Claude Code is still starting it; if it asks a question, the Claude panel shows it.</p>
            ) : (
              <p className="muted small">
                The session designs the path, runs it with xTB and pushes it to GitHub. While this investigation is
                open, the app looks for it every few minutes and imports it as a new node between the two ends; Cloud
                jobs shows where it stands.
              </p>
            )}
            {job.warning && <div className="notice warn">{job.warning}</div>}
          </div>
        )}
      </Modal>
    )
  }

  const row = (h: Held, index: number) => (
    <tr key={`${h.end}:${h.atoms.join('-')}`}>
      <td>
        <input
          type="checkbox"
          aria-label={`Hold ${h.kind} ${h.atoms.join('–')}`}
          checked={h.ticked}
          onChange={(event) => setHeld(held.map((x, i) => (i === index ? { ...x, ticked: event.target.checked } : x)))}
        />
      </td>
      <td>
        {h.kind} {h.atoms.map((a) => `${startAtoms[a - 1]?.element ?? ''}${a}`).join('–')}
      </td>
      <td className="num">{format(h.kind, h.start_value)}</td>
      <td className="num">{format(h.kind, h.end_value)}</td>
      <td className="muted small">{h.why}</td>
    </tr>
  )

  const endName = (which: 'start' | 'end') => (plan ? plan[which].label : '')

  return (
    <Modal
      title="Scan path"
      wide
      onClose={onClose}
      actions={
        <>
          <button onClick={swap} title="Run the path the other way">
            Swap ends
          </button>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!plan || busy || sending || missing.length > 0 || driveBad !== undefined}
            onClick={send}
            title={
              missing.length
                ? `Tick the coordinates that make “${missing[0].label}” a TS`
                : driveBad
                  ? `Give numbers for ${driveBad.kind} ${driveBad.atoms.join('–')}`
                  : 'Write the job and start a Claude Code cloud session on it (D93)'
            }
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </>
      }
    >
      {answer?.error && <p role="alert">{answer.error}</p>}
      {plan && (
        <div aria-label="Scan path plan" aria-busy={busy}>
          <p>
            From “{plan.start.label}” to “{plan.end.label}”: GFN2-xTB relaxed scans designed by a Claude Code cloud session, charge {plan.charge},
            multiplicity {plan.multiplicity}. The path comes back as a new node between the two.
          </p>
          {(['start', 'end'] as const).map(
            (which) =>
              plan[which].members.length > 1 && (
                <label className="field" key={which}>
                  <span>
                    {which === 'start' ? 'Start' : 'End'}: member of “{plan[which].group_label}”
                  </span>
                  <select
                    aria-label={`${which === 'start' ? 'Start' : 'End'} member`}
                    value={plan[which].node_id}
                    onChange={(event) => {
                      setMembers({ ...members, [which]: event.target.value })
                      setPairs(null)
                      if (which === 'start') setDrive([])
                    }}
                  >
                    {plan[which].members.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </label>
              ),
          )}
          <section className="scan-path-section" aria-label="Atom match">
            <p aria-label="Match summary">
              {describeMatch(plan.match)}{' '}
              <button className="small" onClick={() => setReviewing(!review)}>
                {review ? 'Hide the match' : 'Review'}
              </button>
            </p>
            {plan.warnings.map((w) => (
              <div key={w} className="notice warn" role="status" aria-label="Scan path warning">
                {w}
              </div>
            ))}
            {plan.match.doubts.length > 0 && (
              <div className="notice warn" role="status" aria-label="Doubts">
                Check this match: {plan.match.doubts.join('; ')}.
              </div>
            )}
            {review && (
              <AtomMatchReview
                key={`${plan.match.start_id}:${plan.match.end_id}:${JSON.stringify(plan.match.fixed)}`}
                match={plan.match}
                startName={endName('start')}
                endName={endName('end')}
                onFix={fix}
              />
            )}
          </section>
          {plan.ts_ends.map((ts) => (
            <section className="scan-path-section" key={ts.end} aria-label={`Held at ${ts.end === 'start' ? 'the start' : 'the end'}`}>
              <p className={ts.guess ? 'notice warn' : undefined}>{tsHeading(ts)}</p>
              <table className="scan-path-held">
                <thead>
                  <tr>
                    <th>Hold</th>
                    <th>Coordinate (start's numbering)</th>
                    <th>At “{plan.start.label}”</th>
                    <th>At “{plan.end.label}”</th>
                    <th />
                  </tr>
                </thead>
                <tbody>{held.map((h, i) => (h.end === ts.end ? row(h, i) : null))}</tbody>
              </table>
            </section>
          ))}
          {plan.ts_ends.length > 0 && (
            <div className="scan-path-add">
              <span className="small">Hold another coordinate at</span>
              <select
                aria-label="Held at"
                value={typedEnd}
                onChange={(event) => setTyped({ ...typed, end: event.target.value as 'start' | 'end' })}
              >
                {plan.ts_ends.map((ts) => (
                  <option key={ts.end} value={ts.end}>
                    {ts.label}
                  </option>
                ))}
              </select>
              <input
                aria-label="Atoms to hold"
                placeholder="atoms, e.g. 3 7"
                value={typed.text}
                onChange={(event) => setTyped({ ...typed, text: event.target.value })}
                onKeyDown={(event) => event.key === 'Enter' && addTyped()}
              />
              <button className="small" onClick={addTyped}>
                Add
              </button>
              {typedError && <span role="alert">{typedError}</span>}
            </div>
          )}
          {plan.ts_ends.length === 0 && <p className="muted small">Neither end is a TS, so no coordinate is held.</p>}
          <section className="scan-path-section" aria-label="Drive these">
            <p>
              <strong>Drive these (optional)</strong>{' '}
              <span className="muted small">
                Empty, the agent designs the path itself. Given, it runs your design first, as given, and its own only if yours misses the quality
                check.
              </span>
            </p>
            {(drive.length > 0 || heldDriven.length > 0) && (
              <table className="scan-path-held">
                <thead>
                  <tr>
                    <th>Coordinate (start's numbering)</th>
                    <th>From</th>
                    <th>To</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {heldDriven.map((h) => (
                    <tr key={`held:${h.end}:${h.atoms.join('-')}`} className="held-driven" aria-label={`Held ${h.kind} ${h.atoms.join('–')}`}>
                      <td>
                        {h.kind} {h.atoms.map((a) => `${startAtoms[a - 1]?.element ?? ''}${a}`).join('–')}
                      </td>
                      <td className="num">{format(h.kind, h.start_value)}</td>
                      <td className="num">{format(h.kind, h.end_value)}</td>
                      <td className="small">held at the TS, so driven with these</td>
                    </tr>
                  ))}
                  {drive.map((d, i) => {
                    const name = `${d.kind} ${d.atoms.join('–')}`
                    const from = number(d.from)
                    return (
                      <tr key={d.atoms.join('-')} aria-label={`Drive ${name}`}>
                        <td>
                          {driveOrder === 'staged' && `${i + 1}. `}
                          {d.kind} {d.atoms.map((a) => `${startAtoms[a - 1]?.element ?? ''}${a}`).join('–')}
                        </td>
                        <td className="num">
                          <input
                            className="drive-value"
                            aria-label={`From ${name}`}
                            value={d.from}
                            onChange={(event) => changeDrive(i, { from: event.target.value })}
                          />{' '}
                          {unit(d.kind)}
                        </td>
                        <td className="num">
                          <input
                            className="drive-value"
                            aria-label={`To ${name}`}
                            value={d.to}
                            onChange={(event) => changeDrive(i, { to: event.target.value })}
                          />{' '}
                          {unit(d.kind)}
                        </td>
                        <td>
                          {d.kind === 'dihedral' && Number.isFinite(from) && Number.isFinite(number(d.to)) && (
                            <button
                              className="small"
                              title="Turn the other way round: xTB scans a dihedral as written, past ±180° too"
                              onClick={() => {
                                const to = number(d.to)
                                changeDrive(i, {
                                  to: fixed(d.kind, to > from ? to - 360 : to + 360),
                                })
                              }}
                            >
                              Other way round
                            </button>
                          )}{' '}
                          {driveOrder === 'staged' && i > 0 && (
                            <button
                              className="small"
                              aria-label={`Move ${name} up`}
                              onClick={() => setDrive([...drive.slice(0, i - 1), d, drive[i - 1], ...drive.slice(i + 1)])}
                            >
                              ↑
                            </button>
                          )}{' '}
                          <button className="small" aria-label={`Remove ${name}`} onClick={() => setDrive(drive.filter((_, k) => k !== i))}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
            <div className="scan-path-add">
              <input
                aria-label="Atoms to drive"
                placeholder="atoms, e.g. 2 1 6 3"
                value={driveText}
                onChange={(event) => setDriveText(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && addDrive()}
              />
              <button className="small" aria-label="Add a coordinate to drive" onClick={addDrive}>
                Add
              </button>
              {driveError && <span role="alert">{driveError}</span>}
            </div>
            {drive.length + heldDriven.length > 1 && drive.length > 0 && (
              <div className="scan-path-add" role="radiogroup" aria-label="Drive them">
                <label>
                  <input type="radio" name="drive-order" checked={driveOrder === 'together'} onChange={() => setDriveOrder('together')} /> together
                  (one concerted scan)
                </label>
                <label>
                  <input type="radio" name="drive-order" checked={driveOrder === 'staged'} onChange={() => setDriveOrder('staged')} /> in this order
                  (one stage per row)
                </label>
              </div>
            )}
          </section>
          <label className="field">
            <span>Solvent (xTB ALPB)</span>
            <select aria-label="Solvent" value={chosenSolvent ?? ''} onChange={(event) => setSolvent(event.target.value || null)}>
              <option value="">Gas phase</option>
              {plan.solvents.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {sendError && <p role="alert">{sendError}</p>}
        </div>
      )}
      {!plan && !answer?.error && <p className="muted">Matching the atoms…</p>}
    </Modal>
  )
}
