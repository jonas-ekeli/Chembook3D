import { useEffect, useState } from 'react'
import { api, type LocalJob, type LocalTools, type ScanPathPlan } from '../api'
import { describeMatch } from '../atomMatch'
import { parseXyz } from '../chem'
import { duration } from '../localJobs'
import { AtomMatchReview } from './AtomMatchDialog'
import { Modal } from './Modal'
import { errorText } from '../util'

/** D238: a TS guess between two structures joined by an edge, run on this computer with
 * MACE-OMOL-0 (D237): both ends optimized, an IDPP band between them, Sella's saddle search
 * from its highest image and a check of the saddle's mode on the atoms that react. The dialog
 * shows the atom match (D113, reviewed when doubtful), the bonds that change and the estimate,
 * and starts the job; it comes back as a new TS node between the two. */
export function TsGuessDialog({
  startId,
  endId,
  onClose,
  onStarted,
  onSettings,
}: {
  startId: string
  endId: string
  onClose: () => void
  onStarted: (job: LocalJob) => void
  /** Settings › Local calculations, where MACE-OMOL-0 is installed */
  onSettings?: () => void
}) {
  const [ends, setEnds] = useState({ start: startId, end: endId })
  const [members, setMembers] = useState<{ start: string | null; end: string | null }>({ start: null, end: null })
  const [pairs, setPairs] = useState<[number, number][]>([])
  const [answer, setAnswer] = useState<{ key: string; plan: ScanPathPlan | null; error: string | null } | null>(null)
  const [tools, setTools] = useState<LocalTools | null>(null)
  const [reviewing, setReviewing] = useState<boolean | null>(null)
  const [correct, setCorrect] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)
  const key = JSON.stringify([ends, members, pairs])
  const plan = answer?.plan ?? null
  const busy = answer?.key !== key
  const atoms = plan ? parseXyz(plan.match.start_xyz) : []

  useEffect(() => {
    let cancelled = false
    api
      .scanPathPlan({
        start_id: ends.start,
        end_id: ends.end,
        pairs,
        start_member_id: members.start,
        end_member_id: members.end,
      })
      .then(
        (result) => !cancelled && setAnswer({ key, plan: result, error: null }),
        (err: unknown) => !cancelled && setAnswer({ key, plan: null, error: errorText(err) }),
      )
    return () => {
      cancelled = true
    }
    // `key` stands for the ends, members and pairs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const count = atoms.length
  useEffect(() => {
    if (!count) return
    let cancelled = false
    api.localTools(count).then(
      (result) => !cancelled && setTools(result),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [count])

  const installed = tools ? tools.methods.some((m) => m.value === 'mace-omol') : true
  const review = reviewing ?? (plan ? !plan.match.confident : false)
  const name = (n: number) => `${atoms[n - 1]?.element ?? ''}${n}`
  const changing = plan ? [...plan.match.formed.map((p) => ({ p, how: 'forms' })), ...plan.match.broken.map((p) => ({ p, how: 'breaks' }))] : []
  const refusal = plan?.species
    ? 'A TS guess needs the same atoms at both ends; this edge has a species joining or leaving. Make a node of the complex with the species close by first.'
    : null

  const swap = () => {
    setEnds({ start: ends.end, end: ends.start })
    setMembers({ start: members.end, end: members.start })
    setPairs([])
    setReviewing(null)
  }

  const start = () => {
    if (!plan) return
    setStarting(true)
    setStartError(null)
    api
      .startTsGuess({
        start_id: ends.start,
        end_id: ends.end,
        start_member_id: members.start,
        end_member_id: members.end,
        pairs: pairs.length ? pairs : null,
        correct,
      })
      .then(
        (job) => {
          setStarting(false)
          onStarted(job)
          onClose()
        },
        (err: unknown) => {
          setStarting(false)
          setStartError(errorText(err))
        },
      )
  }

  return (
    <Modal
      title="TS guess on this computer"
      wide
      onClose={onClose}
      actions={
        <>
          <button onClick={swap} title="Use the other end's atom numbering">
            Swap ends
          </button>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!plan || busy || starting || !installed || refusal !== null}
            onClick={start}
            title={installed ? 'Start the job on this computer (D238)' : 'Install MACE-OMOL-0 first'}
          >
            {starting ? 'Starting…' : 'Start'}
          </button>
        </>
      }
    >
      {answer?.error && <p role="alert">{answer.error}</p>}
      {!installed && (
        <div className="notice warn" role="status">
          A TS guess runs with MACE-OMOL-0, an optional download.{' '}
          {onSettings ? (
            <button className="small" onClick={onSettings}>
              Install it in Settings
            </button>
          ) : (
            'Install it in Settings › Local calculations.'
          )}
        </div>
      )}
      {plan && (
        <div aria-label="TS guess plan" aria-busy={busy}>
          <p>
            From “{plan.start.label}” to “{plan.end.label}”, charge {plan.charge}, multiplicity {plan.multiplicity}: both ends
            optimized with MACE-OMOL-0, an IDPP band between them and Sella's saddle search from its highest image, then a check
            that the saddle's imaginary mode is on the atoms that react. It comes back as a new TS node between the two, a
            starting structure for a TS optimization at your own level.
          </p>
          {tools?.ts_guess && (
            <p className="muted small" aria-label="TS guess estimate">
              About {duration(tools.ts_guess.seconds)} for {count} atoms on {tools.cores} core{tools.cores === 1 ? '' : 's'}.
            </p>
          )}
          <label className="check">
            <input type="checkbox" checked={correct} onChange={(event) => setCorrect(event.target.checked)} />
            Correct the saddle with PBE single points
          </label>
          <p className="muted small" aria-label="Correction">
            MACE-OMOL-0 may place the TS at a forming bond that is too short (on a Ru [2+2] test, 2.18 Å against DFT's 2.56 Å). This holds that bond at six
            lengths 0.1 Å apart from the saddle toward the separated end, minimizes each with MACE-OMOL-0, and keeps the one with
            the highest PBE-D3M(BJ)/def2-SVP energy as a second TS node; the raw guess stays.
            {tools?.ts_guess && ` About ${duration(tools.ts_guess.correction_seconds)} more.`}
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
                      setPairs([])
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
            {plan.match.doubts.length > 0 && (
              <div className="notice warn" role="status" aria-label="Doubts">
                Check this match: {plan.match.doubts.join('; ')}.
              </div>
            )}
            {review && (
              <AtomMatchReview
                key={`${plan.match.start_id}:${plan.match.end_id}:${JSON.stringify(plan.match.fixed)}`}
                match={plan.match}
                startName={plan.start.label}
                endName={plan.end.label}
                onFix={setPairs}
              />
            )}
          </section>
          <section className="scan-path-section" aria-label="Reacting atoms">
            {changing.length ? (
              <p>
                The mode is checked on {changing.map(({ p, how }) => `${name(p[0])}–${name(p[1])} (${how})`).join(', ')}, numbered as
                in “{plan.start.label}”.
              </p>
            ) : (
              <p className="notice warn" role="status">
                No bond forms or breaks between the ends: the mode is checked on the atoms that move most.
              </p>
            )}
          </section>
          {refusal && (
            <div className="notice warn" role="status">
              {refusal}
            </div>
          )}
        </div>
      )}
      {startError && <p role="alert">{startError}</p>}
    </Modal>
  )
}
