import { useEffect, useMemo, useState } from 'react'
import { api, type AtomMatch, type Node } from '../api'
import { defaultRotation, parseXyz } from '../chem'
import { describeMatch } from '../atomMatch'
import { downloadBlob, errorText, fileName } from '../util'
import { Modal } from './Modal'
import { Viewer3D, type AtomMarks } from './Viewer3D'
import { nodeName } from '../names'

const FORMED = '#2f9e44'
const BROKEN = '#e03131'
const CHANGED = '#f59f00'
const INVERTED = '#ae3ec9'
const FIXED = '#74c0fc'
const PICKED = '#ffd43b'

type Numbers = 'changed' | 'heavy' | 'all'


/** D113: the two ends side by side, each start atom and the end atom matched to it carrying the
 * same number, bonds that form (green) or break (red) dashed, and pairs fixed by clicking one
 * atom in each view. The end is shown renumbered and placed on the start, so both views turn
 * the same way. */
export function AtomMatchReview({
  match,
  startName,
  endName,
  titles,
  onFix,
}: {
  match: AtomMatch
  startName: string
  endName: string
  /** The two views' headings, when not "Start" and "End" (D242's standalone window). */
  titles?: [string, string]
  /** The pairs to keep, [start atom, end atom], 1-based. */
  onFix: (pairs: [number, number][]) => void
}) {
  const [numbers, setNumbers] = useState<Numbers>('heavy')
  // The atom clicked in each view, 0-based in the start's order, waiting for its partner.
  const [picked, setPicked] = useState<{ start: number | null; end: number | null }>({ start: null, end: null })
  const atoms = useMemo(() => parseXyz(match.start_xyz), [match.start_xyz])
  const rotation = useMemo(() => defaultRotation(atoms), [atoms])

  const fixedStarts = new Set(match.fixed.map(([a]) => a - 1))
  const changed = new Set([...match.formed.flat(), ...match.broken.flat()].map((n) => n - 1))
  const inverted = new Set(match.inverted.map((n) => n - 1))
  const lines = [
    ...match.formed.map(([a, b]) => ({ from: a - 1, to: b - 1, colour: FORMED })),
    ...match.broken.map(([a, b]) => ({ from: a - 1, to: b - 1, colour: BROKEN })),
  ]
  const marks = (side: 'start' | 'end'): AtomMarks => ({
    lines,
    labels: atoms.flatMap((atom, i) => {
      const special = changed.has(i) || inverted.has(i) || fixedStarts.has(i) || picked[side] === i
      const shown = numbers === 'all' || special || (numbers === 'heavy' && atom.element !== 'H')
      if (!shown) return []
      const colour =
        picked[side] === i
          ? PICKED
          : inverted.has(i)
            ? INVERTED
            : changed.has(i)
              ? CHANGED
              : fixedStarts.has(i)
                ? FIXED
                : undefined
      return [{ index: i, text: String(i + 1), colour, highlight: special }]
    }),
  })

  const pick = (side: 'start' | 'end', index: number) => {
    const next = { ...picked, [side]: picked[side] === index ? null : index }
    if (next.start !== null && next.end !== null) {
      const pair: [number, number] = [next.start + 1, match.mapping[next.end]]
      const kept = match.fixed.filter(([a, b]) => a !== pair[0] && b !== pair[1])
      setPicked({ start: null, end: null })
      onFix([...kept, pair])
      return
    }
    setPicked(next)
  }

  const describePick = () => {
    if (picked.start !== null) return `Start atom ${picked.start + 1} chosen: click its partner in “${endName}”.`
    if (picked.end !== null) {
      return `Atom ${match.mapping[picked.end]} of “${endName}” chosen: click its partner in “${startName}”.`
    }
    return 'To fix a pair by hand, click an atom in each view.'
  }

  return (
    <div className="atom-match" aria-label="Atom match review">
      <div className="atom-match-views">
        <div>
          <h3>{titles?.[0] ?? `Start: ${startName}`}</h3>
          <Viewer3D
            models={[{ xyz: match.start_xyz }]}
            rotation={rotation}
            picking={{ atoms: [], onToggle: (index) => pick('start', index) }}
            marks={marks('start')}
          />
        </div>
        <div>
          <h3>{titles?.[1] ?? `End: ${endName}, numbered as the start`}</h3>
          <Viewer3D
            models={[{ xyz: match.renumbered_xyz }]}
            rotation={rotation}
            picking={{ atoms: [], onToggle: (index) => pick('end', index) }}
            marks={marks('end')}
          />
        </div>
      </div>
      <div className="atom-match-options">
        <label className="field">
          <span>Numbers on</span>
          <select aria-label="Numbers on" value={numbers} onChange={(event) => setNumbers(event.target.value as Numbers)}>
            <option value="changed">Changed atoms only</option>
            <option value="heavy">All but hydrogens</option>
            <option value="all">All atoms</option>
          </select>
        </label>
        <span className="small">
          <span className="swatch" style={{ background: FORMED }} /> forms <span className="swatch" style={{ background: BROKEN }} />{' '}
          breaks <span className="swatch" style={{ background: INVERTED }} /> inverts <span className="swatch" style={{ background: FIXED }} />{' '}
          fixed by hand
        </span>
      </div>
      <p className="muted small" aria-label="Pick">
        {describePick()}
      </p>
      {match.fixed.length > 0 && (
        <div className="atom-match-fixed" aria-label="Fixed pairs">
          <span className="small">Fixed by hand:</span>
          {match.fixed.map(([a, b]) => (
            <button
              key={`${a}-${b}`}
              className="small"
              title="Let the match choose this atom again"
              onClick={() => onFix(match.fixed.filter(([x]) => x !== a))}
            >
              {atoms[a - 1]?.element}
              {a} ↔ {b} ×
            </button>
          ))}
          <button className="small" onClick={() => onFix([])}>
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}

/** D113, A59: match the atoms of two selected structures, review the match side by side and fix
 * pairs by hand. D242, A176: which one is renumbered is chosen by name; Save gives it the other's
 * numbering at its own coordinates (in place without calculations, else as a new derived node,
 * ID-4, ID-5), and the download gives it placed on the other. */
export function AtomMatchDialog({
  nodes,
  onClose,
  onSaved,
}: {
  nodes: [Node, Node]
  onClose: () => void
  /** D242: the node saved in the new numbering, and whether it is a new node. */
  onSaved?: (node: Node, derived: boolean) => void
}) {
  // The first keeps its numbering (the reference); the second is renumbered.
  const [[start, end], setEnds] = useState(nodes)
  const [pairs, setPairs] = useState<[number, number][]>([])
  // The answer and the request it answers: a newer request shows the older answer as busy.
  const [answer, setAnswer] = useState<{ key: string; match: AtomMatch | null; error: string | null } | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  // A doubtful match is saved by a second click (D242).
  const [confirming, setConfirming] = useState(false)
  const key = JSON.stringify([start.id, end.id, pairs])
  const busy = answer?.key !== key
  const match = answer?.match ?? null
  const error = answer?.error ?? null
  const unchanged = !!match && match.mapping.every((j, i) => j === i + 1)

  useEffect(() => {
    let cancelled = false
    setConfirming(false)
    api.atomMatch(start.id, end.id, pairs).then(
      (result) => !cancelled && setAnswer({ key, match: result, error: null }),
      (err: unknown) => !cancelled && setAnswer({ key, match: null, error: errorText(err) }),
    )
    return () => {
      cancelled = true
    }
    // `key` stands for the ends and pairs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const renumber = (which: Node) => {
    if (which.id === end.id) return
    setPairs([])
    setSaveError(null)
    setEnds([end, start])
  }

  const download = () => {
    if (!match) return
    downloadBlob(
      new Blob([match.renumbered_xyz], { type: 'chemical/x-xyz' }),
      fileName(`${nodeName(end)} numbered as ${nodeName(start)}.xyz`, 'renumbered.xyz'),
    )
  }

  const save = () => {
    if (!match || busy || unchanged) return
    if (match.doubts.length > 0 && !confirming) {
      setConfirming(true)
      return
    }
    setSaving(true)
    setSaveError(null)
    api.saveAtomMatch(start.id, end.id, pairs).then(
      (result) => {
        setSaving(false)
        onSaved?.(result.node, result.derived)
        onClose()
      },
      (err: unknown) => {
        setSaving(false)
        setConfirming(false)
        setSaveError(errorText(err))
      },
    )
  }

  const derives = end.calculation_count > 0
  const saveLabel = confirming ? 'Save anyway' : derives ? 'Save as new node' : `Save “${nodeName(end)}” renumbered`
  const saveTitle = unchanged
    ? `“${nodeName(end)}” is already numbered as “${nodeName(start)}”`
    : derives
      ? `“${nodeName(end)}” has calculations, so it stays as it is and a new node “${nodeName(end)} (numbered as ${nodeName(start)})” gets the new order (ID-5)`
      : `“${nodeName(end)}” takes the atom order of “${nodeName(start)}” at its own coordinates; its atoms in alignment sets and steric profiles follow`

  return (
    <Modal
      title="Match atoms"
      wide
      onClose={onClose}
      actions={
        <>
          <button disabled={!match} onClick={download} title="The renumbered structure placed on the other one, as .xyz">
            Download renumbered
          </button>
          <button onClick={onClose}>Close</button>
          <button className="primary" disabled={!match || busy || unchanged || saving} onClick={save} title={saveTitle}>
            {saveLabel}
          </button>
        </>
      }
    >
      <div className="atom-match-direction" role="radiogroup" aria-label="Renumber">
        {[nodes[1], nodes[0]].map((which) => {
          const other = which.id === end.id ? start : end
          return (
            <label key={which.id} className="check">
              <input type="radio" name="renumber" checked={which.id === end.id} onChange={() => renumber(which)} />
              <span>
                Renumber “{nodeName(which)}” to match “{nodeName(other)}”
              </span>
            </label>
          )
        })}
      </div>
      <p className="muted small">
        {derives
          ? `“${nodeName(end)}” has calculations, so saving keeps it as it is and makes a new node with the new order.`
          : `Saving changes the atom order of “${nodeName(end)}”, not its coordinates.`}{' '}
        “{nodeName(start)}” is not changed.
      </p>
      {match && (
        <p aria-label="Match summary" aria-busy={busy}>
          {describeMatch(match)}
          {unchanged && ' Nothing to save.'}
        </p>
      )}
      {match && match.doubts.length > 0 && (
        <div className="notice warn" role="status" aria-label="Doubts">
          Check this match: {match.doubts.join('; ')}.
          {confirming && ' Save anyway?'}
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {saveError && <p role="alert">{saveError}</p>}
      {match && (
        <AtomMatchReview
          key={`${match.start_id}:${match.end_id}:${JSON.stringify(match.fixed)}`}
          match={match}
          startName={nodeName(start)}
          endName={nodeName(end)}
          titles={[`${nodeName(start)}, keeps its numbering`, `${nodeName(end)}, renumbered to match`]}
          onFix={setPairs}
        />
      )}
    </Modal>
  )
}
