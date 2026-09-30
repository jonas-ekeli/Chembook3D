import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type AlignmentSet, type Node, type Overlay, type OverlayAlign, type OverlayRequest } from '../api'
import { formatAtomList, parseAtomList, parseXyz } from '../chem'
import { Modal } from './Modal'
import { Viewer3D } from './Viewer3D'

/** A31: at most this many structures, for legibility. */
export const MAX_OVERLAY = 12

// The reference keeps element colours; the others get one colour each.
const COLOURS = ['#d6336c', '#1c7ed6', '#2f9e44', '#f08c00', '#7048e8', '#0c8599', '#e8590c', '#5c940d', '#c2255c', '#364fc7', '#862e9c']
const REFERENCE_SWATCH = '#707070'

const SHARED = '*' // the key of the one list that serves every structure

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const nodeName = (n: Node) => n.label || 'Untitled node'
const rmsd = (value: number | null) => (value === null ? '—' : `${value.toFixed(3)} Å`)

function save(url: string, name: string) {
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
}

/** FR-3D-04, FR-3D-07, D80: 2 to 12 structures superposed on one reference, aligned on all
 * atoms, on chosen atoms, or not at all, with an optional mirror image. Atom lists can be
 * saved as a named alignment set and reused. */
export function OverlayDialog({ nodes, onClose }: { nodes: Node[]; onClose: () => void }) {
  const [referenceId, setReferenceId] = useState(nodes[0].id)
  const reference = nodes.find((n) => n.id === referenceId) ?? nodes[0]
  // D80: one list serves all structures when they have the same elements in the same order.
  const sameAtoms = useMemo(() => {
    const sequences = nodes.map((n) => parseXyz(n.xyz ?? '').map((a) => a.element).join(' '))
    return sequences.every((s) => s === sequences[0])
  }, [nodes])
  const [align, setAlign] = useState<OverlayAlign>(sameAtoms ? 'all' : 'atoms')
  const [split, setSplit] = useState(!sameAtoms)
  const shared = sameAtoms && !split
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [mirror, setMirror] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [picking, setPicking] = useState<string | null>(null)
  const [overlay, setOverlay] = useState<Overlay | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sets, setSets] = useState<AlignmentSet[]>([])
  const [setId, setSetId] = useState('')
  const [naming, setNaming] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const image = useRef<(() => string) | null>(null)

  const chosenSet = sets.find((s) => s.id === setId) ?? null
  const keys = shared ? [SHARED] : nodes.map((n) => n.id)

  /** Each structure's atom numbers, or the first reason a list cannot be read. */
  const parsed = (): { atoms: Record<string, number[]> } | { error: string } => {
    const atoms: Record<string, number[]> = {}
    for (const key of keys) {
      const result = parseAtomList(texts[key] ?? '')
      const name = key === SHARED ? 'All structures' : nodeName(nodes.find((n) => n.id === key)!)
      if ('error' in result) return { error: `${name}: ${result.error}` }
      if (result.numbers.length === 0) return { error: `${name}: choose the atoms to align on.` }
      if (key === SHARED) nodes.forEach((n) => (atoms[n.id] = result.numbers))
      else atoms[key] = result.numbers
    }
    return { atoms }
  }

  const run = (changes: { align?: OverlayAlign; referenceId?: string; mirror?: boolean } = {}) => {
    const mode = changes.align ?? align
    const body: OverlayRequest = {
      node_ids: nodes.map((n) => n.id),
      reference_id: changes.referenceId ?? referenceId,
      align: mode,
      allow_mirror: changes.mirror ?? mirror,
    }
    if (mode === 'atoms') {
      const lists = parsed()
      if ('error' in lists) {
        setError(lists.error)
        return
      }
      body.atoms = lists.atoms
    }
    setError(null)
    api.overlay(body).then(setOverlay, (err: unknown) => setError(errorText(err)))
  }

  useEffect(() => {
    api.alignmentSets().then(setSets, () => setSets([]))
    if (align === 'all' || align === 'none') run()
    // Only on opening; later runs follow the user's changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadSet = (id: string) => {
    setSetId(id)
    setNotice(null)
    const found = sets.find((s) => s.id === id)
    if (!found) return
    const lists = nodes.map((n) => found.atoms[n.id])
    const next: Record<string, string> = {}
    nodes.forEach((n, i) => (next[n.id] = lists[i] ? formatAtomList(lists[i]) : ''))
    const same = lists.every((l) => l && l.join(',') === lists[0]!.join(','))
    if (sameAtoms && same) next[SHARED] = next[nodes[0].id]
    setSplit(!(sameAtoms && same))
    setTexts(next)
    setAlign('atoms')
    const missing = nodes.filter((n) => !found.atoms[n.id])
    if (missing.length) {
      setNotice(
        `${missing.map(nodeName).join(', ')} ${missing.length === 1 ? 'is' : 'are'} not in “${found.name}” yet. Pick ${missing.length === 1 ? 'its' : 'their'} atoms, then update the set.`,
      )
      setError(null)
      return
    }
    const atoms: Record<string, number[]> = {}
    nodes.forEach((n) => (atoms[n.id] = found.atoms[n.id]))
    setError(null)
    api
      .overlay({ node_ids: nodes.map((n) => n.id), reference_id: referenceId, align: 'atoms', atoms, allow_mirror: mirror })
      .then(setOverlay, (err: unknown) => setError(errorText(err)))
  }

  const saveSet = (name: string | null) => {
    const lists = parsed()
    if ('error' in lists) {
      setError(lists.error)
      return
    }
    const done = (saved: AlignmentSet) => {
      setSets((current) => [...current.filter((s) => s.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)))
      setSetId(saved.id)
      setNaming(null)
      setError(null)
      setNotice(`Saved “${saved.name}”.`)
    }
    const request =
      name === null && chosenSet
        ? api.updateAlignmentSet(chosenSet.id, { atoms: lists.atoms })
        : api.createAlignmentSet(name ?? '', lists.atoms)
    request.then(done, (err: unknown) => setError(errorText(err)))
  }

  const deleteSet = () => {
    if (!chosenSet) return
    api.deleteAlignmentSet(chosenSet.id).then(
      () => {
        setSets((current) => current.filter((s) => s.id !== chosenSet.id))
        setSetId('')
        setNotice(`Deleted “${chosenSet.name}”.`)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  // Picking: the structure is shown alone and clicked atoms join its list in order.
  const pickNode = picking === SHARED ? reference : nodes.find((n) => n.id === picking) ?? null
  const pickedAtoms = picking ? (() => {
    const result = parseAtomList(texts[picking] ?? '')
    return 'numbers' in result ? result.numbers.map((n) => n - 1) : []
  })() : []
  const togglePick = (index: number) => {
    if (!picking) return
    const current = parseAtomList(texts[picking] ?? '')
    const numbers = 'numbers' in current ? current.numbers : []
    const next = numbers.includes(index + 1) ? numbers.filter((n) => n !== index + 1) : [...numbers, index + 1]
    setTexts((t) => ({ ...t, [picking]: formatAtomList(next) }))
  }

  const colourOf = new Map<string, string | undefined>()
  let next = 0
  for (const n of nodes) colourOf.set(n.id, n.id === referenceId ? undefined : COLOURS[next++ % COLOURS.length])
  const placed = overlay ? new Map(overlay.structures.map((s) => [s.node_id, s])) : null
  const ordered = overlay
    ? [...overlay.structures].sort((a, b) => Number(b.reference) - Number(a.reference))
    : []
  const models = pickNode
    ? [{ xyz: pickNode.xyz ?? '' }]
    : ordered.map((s) => ({ xyz: s.xyz, colour: colourOf.get(s.node_id), hidden: hidden.has(s.node_id) }))
  const centredOnly = overlay?.structures.filter((s) => !s.reference && !s.rotated && overlay.align === 'all') ?? []

  const saveXyz = () => {
    if (!overlay) return
    const text = ordered.map((s) => s.xyz.trimEnd()).join('\n') + '\n'
    const url = URL.createObjectURL(new Blob([text], { type: 'chemical/x-xyz' }))
    save(url, 'overlay.xyz')
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <Modal
      title="Overlay in 3D"
      wide
      onClose={onClose}
      actions={
        <>
          <button disabled={!overlay || !!picking} onClick={() => image.current && save(image.current(), 'overlay.png')}>
            Save image
          </button>
          <button disabled={!overlay} onClick={saveXyz} title="The structures as placed, one after another">
            Save .xyz
          </button>
          <button onClick={onClose}>Close</button>
        </>
      }
    >
      <div className="overlay-options" aria-label="Overlay options">
        <label className="field">
          <span>Reference</span>
          <select
            aria-label="Reference"
            value={referenceId}
            onChange={(event) => {
              setReferenceId(event.target.value)
              run({ referenceId: event.target.value })
            }}
          >
            {nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {nodeName(n)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Align on</span>
          <select
            aria-label="Align on"
            value={align}
            onChange={(event) => {
              const mode = event.target.value as OverlayAlign
              setAlign(mode)
              if (mode !== 'atoms') run({ align: mode })
              else if (keys.every((k) => (texts[k] ?? '').trim())) run({ align: mode })
            }}
          >
            <option value="all">All atoms</option>
            <option value="atoms">Chosen atoms</option>
            <option value="none">No alignment (stored coordinates)</option>
          </select>
        </label>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Allow mirror image"
            checked={mirror}
            onChange={(event) => {
              setMirror(event.target.checked)
              run({ mirror: event.target.checked })
            }}
          />
          <span>Allow mirror image</span>
        </label>
      </div>

      {align === 'atoms' && (
        <section className="overlay-atoms" aria-label="Alignment atoms">
          <div className="overlay-set">
            <label className="field">
              <span>Alignment set</span>
              <select aria-label="Alignment set" value={setId} onChange={(event) => loadSet(event.target.value)}>
                <option value="">None</option>
                {sets.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            {chosenSet && (
              <>
                <button className="small" onClick={() => saveSet(null)} title="Save these atom lists in the set">
                  Update “{chosenSet.name}”
                </button>
                <button className="small danger" onClick={deleteSet}>
                  Delete set
                </button>
              </>
            )}
            {naming === null ? (
              <button className="small" onClick={() => setNaming('')}>
                Save as new set…
              </button>
            ) : (
              <>
                <input
                  aria-label="Set name"
                  placeholder="e.g. Ru–CAAC core"
                  value={naming}
                  autoFocus
                  onChange={(event) => setNaming(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && saveSet(naming)}
                />
                <button className="small primary" onClick={() => saveSet(naming)}>
                  Save set
                </button>
                <button className="small" onClick={() => setNaming(null)}>
                  Cancel
                </button>
              </>
            )}
          </div>
          {sameAtoms && (
            <label className="check">
              <input
                type="checkbox"
                checked={split}
                onChange={(event) => {
                  const on = event.target.checked
                  setSplit(on)
                  if (on) setTexts((t) => Object.fromEntries([...Object.entries(t), ...nodes.map((n) => [n.id, t[SHARED] ?? ''])]))
                  else setTexts((t) => ({ ...t, [SHARED]: t[referenceId] ?? '' }))
                }}
              />
              <span>Different atoms per structure</span>
            </label>
          )}
          <p className="muted small">
            Numbers and ranges such as 1-12, 15, or click “Pick” and then the atoms in the view.{' '}
            {shared ? 'The structures share their numbering, so one list serves all.' : 'The lists are paired in order with the reference’s, and paired atoms must be the same element.'}{' '}
            At least three atoms.
          </p>
          <table className="overlay-lists">
            <tbody>
              {keys.map((key) => {
                const name = key === SHARED ? 'All structures' : nodeName(nodes.find((n) => n.id === key)!)
                return (
                  <tr key={key}>
                    <td>{name}</td>
                    <td>
                      <input
                        aria-label={`Atoms of ${name}`}
                        className="mono"
                        value={texts[key] ?? ''}
                        onChange={(event) => setTexts((t) => ({ ...t, [key]: event.target.value }))}
                        onKeyDown={(event) => event.key === 'Enter' && run()}
                      />
                    </td>
                    <td>
                      <button
                        className="small"
                        aria-pressed={picking === key}
                        onClick={() => {
                          if (picking === key) {
                            setPicking(null)
                            run()
                          } else setPicking(key)
                        }}
                      >
                        {picking === key ? 'Done' : 'Pick'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          <button className="primary small" disabled={!!picking} onClick={() => run()}>
            Align
          </button>
        </section>
      )}

      {pickNode && (
        <p className="muted small" aria-label="Picking">
          Click the atoms of {nodeName(pickNode)} in order; click one again to take it out. Press Done when finished.
        </p>
      )}
      {notice && <p className="muted small">{notice}</p>}
      {error && <p role="alert">{error}</p>}

      {overlay && !pickNode && (
        <table className="overlay-legend" aria-label="Overlay legend">
          <thead>
            <tr>
              <th>Shown</th>
              <th>Structure</th>
              <th title="Over the atoms aligned on">RMSD, alignment atoms</th>
              <th title="Where the atoms correspond one to one with the reference's">RMSD, all atoms</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => {
              const s = placed?.get(n.id)
              if (!s) return null
              return (
                <tr key={n.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Show ${nodeName(n)}`}
                      checked={!hidden.has(n.id)}
                      onChange={(event) =>
                        setHidden((h) => {
                          const next = new Set(h)
                          if (event.target.checked) next.delete(n.id)
                          else next.add(n.id)
                          return next
                        })
                      }
                    />
                  </td>
                  <td>
                    <span className="swatch" style={{ background: colourOf.get(n.id) ?? REFERENCE_SWATCH }} /> {nodeName(n)}
                  </td>
                  <td className="mono">{s.reference ? '—' : rmsd(s.rmsd_atoms)}</td>
                  <td className="mono">{s.reference ? '—' : rmsd(s.rmsd_all)}</td>
                  <td className="muted small">
                    {s.reference
                      ? 'reference (element colours)'
                      : [s.mirrored ? 'mirror image' : '', overlay.align === 'all' && !s.rotated ? 'centred only' : '']
                          .filter(Boolean)
                          .join(', ')}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {centredOnly.length > 0 && (
        <p className="muted small" aria-label="Overlay note">
          {centredOnly.map((s) => s.label).join(', ')} {centredOnly.length === 1 ? 'differs' : 'differ'} from the reference in
          element or order, so {centredOnly.length === 1 ? 'it is' : 'they are'} only centred on it. Align on chosen atoms to
          superpose {centredOnly.length === 1 ? 'it' : 'them'}.
        </p>
      )}
      {(overlay || pickNode) && (
        <div className="overlay-viewer">
          <Viewer3D
            models={models}
            imageRef={image}
            picking={pickNode ? { atoms: pickedAtoms, onToggle: togglePick } : undefined}
          />
        </div>
      )}
    </Modal>
  )
}
