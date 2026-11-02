import { useEffect, useMemo, useState } from 'react'
import { api, type LocalCore } from '../api'
import { formatAtomList, parseAtomList } from '../chem'
import { colourToken, errorText } from '../util'
import { Viewer3D } from './Viewer3D'

/** What the dialog starts a job with: the core's atoms from 0, and whether it can be used. */
export type CoreChoice = { atoms: number[]; usable: boolean; check: LocalCore | null }

const CHECK_DELAY = 300

/** D214, FR-LOC-05: the DFT core of an ONIOM refine. The app suggests one (from the metal
 * atoms, or from atoms the user names); the user changes it by typing atom numbers or clicking
 * atoms in the 3D view, and sees its size, its link hydrogens and what is wrong with it. */
export function RefineCore({
  nodeId,
  xyz,
  count,
  charge,
  multiplicity,
  onChange,
}: {
  nodeId: string
  xyz: string
  count: number
  charge: number | null
  multiplicity: number | null
  onChange: (choice: CoreChoice) => void
}) {
  const [text, setText] = useState<string | null>(null)
  const [seeds, setSeeds] = useState('')
  const [check, setCheck] = useState<LocalCore | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [noMetal, setNoMetal] = useState(false)

  const read = useMemo((): { error: string } | { atoms: number[] } | null => {
    if (text === null) return null
    const found = parseAtomList(text)
    if ('error' in found) return { error: found.error }
    if (!found.numbers.every((n) => n >= 1 && n <= count)) return { error: `Atoms are numbered 1 to ${count}` }
    return { atoms: [...new Set(found.numbers.map((n) => n - 1))].sort((a, b) => a - b) }
  }, [text, count])

  const suggest = (from: number[] | null) =>
    api.localCore({ node_id: nodeId, seeds: from, charge, multiplicity }).then(
      (found) => {
        setNoMetal(from === null && found.core.length === 0)
        setText(formatAtomList(found.core.map((a) => a + 1)))
        setError(null)
      },
      (err: unknown) => setError(errorText(err)),
    )

  // The first suggestion, from the metal atoms.
  useEffect(() => {
    let stale = false
    api.localCore({ node_id: nodeId }).then(
      (found) => {
        if (stale) return
        setNoMetal(found.core.length === 0)
        setText((current) => current ?? formatAtomList(found.core.map((a) => a + 1)))
      },
      (err: unknown) => !stale && setError(errorText(err)),
    )
    return () => {
      stale = true
    }
  }, [nodeId])

  // The core as typed, checked by the server a moment after the last change.
  const key = read && 'atoms' in read ? read.atoms.join(',') : null
  useEffect(() => {
    if (key === null) {
      onChange({ atoms: [], usable: false, check: null })
      return
    }
    let stale = false
    const atoms = key ? key.split(',').map(Number) : []
    const timer = window.setTimeout(() => {
      api.localCore({ node_id: nodeId, core: atoms, charge, multiplicity }).then(
        (found) => {
          if (stale) return
          setCheck(found)
          onChange({ atoms, usable: found.errors.length === 0, check: found })
        },
        (err: unknown) => {
          if (stale) return
          setError(errorText(err))
          onChange({ atoms, usable: false, check: null })
        },
      )
    }, CHECK_DELAY)
    return () => {
      stale = true
      window.clearTimeout(timer)
    }
  }, [key, nodeId, charge, multiplicity, onChange])

  const toggle = (index: number) => {
    const current = read && 'atoms' in read ? read.atoms : []
    const next = current.includes(index) ? current.filter((a) => a !== index) : [...current, index].sort((a, b) => a - b)
    setText(formatAtomList(next.map((a) => a + 1)))
  }

  const shown = check && key === check.core.join(',') ? check : null
  const marks = useMemo(() => {
    const core = colourToken('--viewer-core')
    const link = colourToken('--viewer-link')
    const atoms = read && 'atoms' in read ? read.atoms : []
    return {
      labels: [
        ...atoms.map((index) => ({ index, text: '', colour: core, highlight: true })),
        ...(shown?.links ?? []).map((l) => ({ index: l.outer, text: '', colour: link, highlight: true })),
      ],
    }
  }, [read, shown])

  const seedsRead = parseAtomList(seeds)
  const seedsValid = seeds.trim() !== '' && !('error' in seedsRead) && seedsRead.numbers.every((n) => n >= 1 && n <= count)

  return (
    <fieldset className="refine-core" aria-label="DFT core">
      <legend>DFT core</legend>
      <div className="refine-core-view">
        <Viewer3D models={[{ xyz }]} picking={{ atoms: [], onToggle: toggle }} marks={marks} keepView />
      </div>
      <p className="muted small">
        Blue atoms are treated with r2SCAN-3c; orange ones stand outside the core and are replaced there by a
        hydrogen. Click an atom to take it in or out.
      </p>
      {noMetal && (
        <p className="notice">
          This structure has no metal to start from. Name the atoms that react and choose Suggest, or type the core.
        </p>
      )}
      <label className="field">
        <span>Core atoms</span>
        <input
          aria-label="Core atoms"
          placeholder="e.g. 1-12, 15"
          value={text ?? ''}
          onChange={(event) => setText(event.target.value)}
        />
      </label>
      <div className="refine-core-seeds">
        <label className="field">
          <span>Suggest from atoms</span>
          <input
            aria-label="Suggest from atoms"
            placeholder="metal atoms"
            value={seeds}
            onChange={(event) => setSeeds(event.target.value)}
          />
        </label>
        <button
          className="small"
          disabled={seeds.trim() !== '' && !seedsValid}
          onClick={() => void suggest(seedsValid && 'numbers' in seedsRead ? seedsRead.numbers.map((n) => n - 1) : null)}
        >
          Suggest
        </button>
      </div>
      {read && 'error' in read && (
        <p className="small cloud-job-problem" role="alert">
          Core atoms: {read.error}
        </p>
      )}
      {shown && (
        <p className="small" aria-label="Core size">
          {shown.core.length} of {shown.atoms} atoms
          {shown.links.length ? ` and ${shown.links.length} link hydrogen${shown.links.length === 1 ? '' : 's'}` : ''} in
          r2SCAN-3c; GFN2-xTB for the whole molecule.
        </p>
      )}
      {shown?.errors.map((e) => (
        <p key={e} className="small cloud-job-problem" role="alert">
          {e}
        </p>
      ))}
      {shown?.warnings.map((w) => (
        <p key={w} className="small muted">
          {w}
        </p>
      ))}
      {error && <p role="alert">{error}</p>}
    </fieldset>
  )
}
