import { useEffect, useState } from 'react'
import {
  api,
  ApiError,
  HARTREE_IN_KCAL_MOL,
  type AtomMatch,
  type HistoryEntry,
  type MergeOptions,
  type MergePreview,
  type Node,
  type NodeMergeUndo,
} from '../api'
import { nodeName } from '../names'
import { errorText } from '../util'
import { AtomMatchReview } from './AtomMatchDialog'
import { Modal } from './Modal'

type Branch = NonNullable<MergeOptions['branch']>

const BRANCHES: { value: Branch; label: string; title: string }[] = [
  { value: 'none', label: 'Shared by both', title: 'On no branch of its own, so both branches pass through it (D67)' },
  { value: 'keep', label: 'Keep its own branch', title: 'The node kept stays on its branch' },
  { value: 'take', label: 'Take the other’s branch', title: 'The node kept moves to the merged node’s branch' },
]

function kcal(value: number | null): string {
  return value === null ? 'n/a' : `${(value * HARTREE_IN_KCAL_MOL).toFixed(1)}`
}

/** D262, FR-NODE-09: merge one node into another holding the same molecule. The first given
 * stays (B) and Swap changes it; the window shows why a merge is refused, what it warns about,
 * the atom match when the two number their atoms otherwise (fix pairs as in Match atoms), both
 * nodes' values at each level they share and whose count, and every calculation, edge and record
 * that moves. Merge is one change with one history entry. A free species is merged into another
 * picked from `candidates`. */
export function MergeNodesDialog({
  nodes,
  candidates = [],
  onClose,
  onMerged,
}: {
  /** The node kept, and the one merged into it (null: picked from `candidates`). */
  nodes: [Node, Node | null]
  candidates?: Node[]
  onClose: () => void
  onMerged: (into: Node, merged: MergePreview) => void
}) {
  const [[into, from], setEnds] = useState<[Node, Node | null]>(nodes)
  const [pairs, setPairs] = useState<[number, number][]>([])
  const [branch, setBranch] = useState<Branch>('none')
  const [anyway, setAnyway] = useState(false)
  const [answer, setAnswer] = useState<{ key: string; preview: MergePreview | null; error: string | null } | null>(null)
  const [review, setReview] = useState<{ key: string; match: AtomMatch } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const key = JSON.stringify([into.id, from?.id ?? null, pairs, branch])
  const loading = !!from && answer?.key !== key
  const preview = answer?.key === key ? answer.preview : null

  useEffect(() => {
    if (!from) return
    let cancelled = false
    setAnyway(false)
    api.previewNodeMerge(from.id, into.id, { pairs, branch }).then(
      (found) => !cancelled && setAnswer({ key, preview: found, error: null }),
      (err: unknown) => !cancelled && setAnswer({ key, preview: null, error: errorText(err) }),
    )
    return () => {
      cancelled = true
    }
    // `key` stands for the two nodes, the pairs and the branch choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // The side-by-side review, only when the two number their atoms otherwise.
  const renumbered = !!preview?.match && !preview.match.same_numbering
  useEffect(() => {
    if (!from || !renumbered) return
    let cancelled = false
    api.atomMatch(into.id, from.id, pairs).then(
      (match) => !cancelled && setReview({ key, match }),
      () => undefined,
    )
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, renumbered])

  const swap = () => {
    if (!from) return
    setPairs([])
    setEnds([from, into])
  }

  const merge = () => {
    if (!from || !preview) return
    setBusy(true)
    setError(null)
    api.mergeNodes(from.id, into.id, { pairs, branch, anyway: anyway || preview.warnings.length === 0 }).then(
      (done) => {
        setBusy(false)
        onMerged(into, done)
        onClose()
      },
      (err: unknown) => {
        setBusy(false)
        setError(errorText(err))
      },
    )
  }

  const blocked = !preview || preview.refusals.length > 0 || (preview.warnings.length > 0 && !anyway)
  const title = from ? `Merge “${nodeName(from)}” into “${nodeName(into)}”` : `Merge into “${nodeName(into)}”`
  const species = into.kind === 'species'
  const others = candidates.filter((n) => n.id !== into.id && n.kind === into.kind)

  return (
    <Modal
      title={title}
      wide
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={blocked || loading || busy}
            onClick={merge}
            title={from ? `“${nodeName(from)}” dissolves; its calculations, edges and records move onto “${nodeName(into)}”` : undefined}
          >
            {preview && preview.warnings.length > 0 ? 'Merge anyway' : 'Merge'}
          </button>
        </>
      }
    >
      <div className="merge-direction" aria-label="Direction">
        {from ? (
          <p>
            “{nodeName(from)}” dissolves into “{nodeName(into)}”, which keeps its id, label, role, status and place.{' '}
            <button className="small" onClick={swap} title="Keep the other one instead">
              Swap
            </button>
          </p>
        ) : (
          <label className="field">
            <span>Merge into “{nodeName(into)}” the {species ? 'free species' : 'node'}</span>
            <select
              aria-label="Node to merge"
              value=""
              onChange={(event) => {
                const picked = others.find((n) => n.id === event.target.value)
                if (picked) setEnds([into, picked])
              }}
            >
              <option value="" disabled>
                Choose…
              </option>
              {others.map((n) => (
                <option key={n.id} value={n.id}>
                  {nodeName(n)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {loading && <p className="muted">Checking the two…</p>}
      {answer?.key === key && answer.error && <p role="alert">{answer.error}</p>}
      {preview && preview.refusals.length > 0 && (
        <div role="alert" aria-label="Refused">
          <p>These two cannot be merged:</p>
          <ul>
            {preview.refusals.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {preview && preview.refusals.length === 0 && (
        <>
          {renumbered && review?.key === key && from && (
            <section aria-label="Atom match">
              <h3>Atoms</h3>
              <p className="small">
                “{nodeName(from)}” numbers its atoms otherwise. Its calculations take the numbering of “{nodeName(into)}”; its files stay
                as they are and are read through the match.
              </p>
              <AtomMatchReview
                match={review.match}
                startName={nodeName(into)}
                endName={nodeName(from)}
                titles={[`“${nodeName(into)}” (kept)`, `“${nodeName(from)}”, numbered as the kept one`]}
                onFix={setPairs}
              />
            </section>
          )}
          {preview.warnings.length > 0 && (
            <div className="merge-warnings" role="alert" aria-label="Warnings">
              <p>Check before merging:</p>
              <ul>
                {preview.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
              <label className="check">
                <input type="checkbox" checked={anyway} onChange={(event) => setAnyway(event.target.checked)} /> Merge anyway
              </label>
            </div>
          )}
          <section aria-label="Geometries">
            <h3>Geometries</h3>
            <ul className="plain small">
              {preview.geometries.map((g) => (
                <li key={g.level ?? 'all'}>
                  {g.level === null ? 'All atoms, after fitting' : `At ${g.level}`}: RMSD {g.rmsd === null ? 'n/a' : `${g.rmsd.toFixed(3)} Å`}
                  {g.warned && <span className="badge warn">apart</span>}
                </li>
              ))}
              {preview.card && (
                <li>
                  “{nodeName(into)}” shows {preview.card.from === 'into' ? 'its own' : `the geometry of “${nodeName(from!)}”`}
                  {preview.card.level ? ` at ${preview.card.level}` : ''}.
                </li>
              )}
            </ul>
          </section>
          {preview.levels.length > 0 && (
            <section aria-label="Shared levels">
              <h3>Levels both have</h3>
              <p className="small muted">The lower energy’s calculations count; the other’s are kept, set aside.</p>
              <table className="steps">
                <thead>
                  <tr>
                    <th>Level</th>
                    <th>Compared</th>
                    <th>“{nodeName(into)}”</th>
                    <th>“{nodeName(from!)}”</th>
                    <th>Difference (kcal/mol)</th>
                    <th>Counts</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.levels.map((lv) => (
                    <tr key={lv.key}>
                      <td>{lv.label}</td>
                      <td>{lv.energy_type}</td>
                      <td className="mono">{lv.into_value === null ? 'n/a' : lv.into_value.toFixed(6)}</td>
                      <td className="mono">{lv.from_value === null ? 'n/a' : lv.from_value.toFixed(6)}</td>
                      <td className="mono">
                        {lv.into_value === null || lv.from_value === null ? 'n/a' : kcal(lv.from_value - lv.into_value)}
                      </td>
                      <td>“{nodeName(lv.counts === 'into' ? into : from!)}”</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
          {preview.branch_choice && (
            <section aria-label="Branch">
              <h3>Branch</h3>
              <p className="small">The two are on different branches (D169: one branch per node).</p>
              <div role="radiogroup" aria-label="Branch choice">
                {BRANCHES.map((b) => (
                  <label key={b.value} className="check" title={b.title}>
                    <input type="radio" name="merge-branch" checked={branch === b.value} onChange={() => setBranch(b.value)} /> {b.label}
                  </label>
                ))}
              </div>
            </section>
          )}
          <section aria-label="What changes">
            <h3>What changes</h3>
            <p className="small muted">One change in the history: Undo merge there brings “{from ? nodeName(from) : ''}” back.</p>
            <ul className="small">
              {preview.changes.map((c) => (
                <li key={c}>{c}</li>
              ))}
              {preview.edges_moved.map((e) => (
                <li key={`moved-${e}`}>Edge {e} moves</li>
              ))}
              {preview.edges_dropped.map((e) => (
                <li key={`dropped-${e}`}>Edge {e} goes</li>
              ))}
            </ul>
          </section>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

function counted(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/** D262h: confirm undoing a merge of two nodes from its history entry, after showing what comes
 * back, or what blocks it (something the merge wrote has changed since). */
export function UndoNodeMergeDialog({
  entry,
  onClose,
  onDone,
}: {
  entry: HistoryEntry
  onClose: () => void
  onDone: (result: NodeMergeUndo) => void
}) {
  const [preview, setPreview] = useState<NodeMergeUndo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.nodeMergeUndoPreview(entry.id).then(setPreview, (err: unknown) => setError(errorText(err)))
  }, [entry.id])

  const value = (entry.new_value ?? {}) as { from_label?: string; into_label?: string }
  const from = preview?.from_label ?? value.from_label ?? 'the merged node'
  const into = preview?.into_label ?? value.into_label ?? 'the kept node'
  const blockers = preview?.blockers ?? []
  const confirm = () => {
    setBusy(true)
    api.undoNodeMerge(entry.id).then(onDone, (err: unknown) => {
      setBusy(false)
      if (err instanceof ApiError && err.blockers && preview) setPreview({ ...preview, blockers: err.blockers })
      else setError(errorText(err))
    })
  }

  return (
    <Modal
      title={`Undo the merge of “${from}” into “${into}”?`}
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>{blockers.length ? 'Close' : 'Cancel'}</button>
          {preview && !blockers.length && (
            <button className="danger" disabled={busy} onClick={confirm}>
              Undo merge
            </button>
          )}
        </>
      }
    >
      {error && <p className="error">{error}</p>}
      {!preview && !error && <p className="muted">Checking what changed since the merge…</p>}
      {preview && blockers.length > 0 && (
        <>
          <p>This merge cannot be undone: what it wrote has changed since.</p>
          <ul aria-label="What blocks the undo">
            {blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
          <p className="muted">Undo or put back those changes first, then try again.</p>
        </>
      )}
      {preview && !blockers.length && (
        <ul aria-label="What the undo does">
          <li>
            “{from}” comes back as it was, with {counted(preview.calculations, 'calculation', 'calculations')} in its own
            numbering.
          </li>
          {preview.edges > 0 && <li>{counted(preview.edges, 'edge goes', 'edges go')} back as they were.</li>}
          <li>“{into}” gets back its tags, notes, geometry and energies from before the merge; later changes stay.</li>
          {preview.records > 0 && <li>{counted(preview.records, 'other record is', 'other records are')} put back.</li>}
        </ul>
      )}
    </Modal>
  )
}
