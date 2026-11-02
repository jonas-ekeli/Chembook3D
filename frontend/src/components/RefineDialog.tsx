import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type LocalJob, type LocalTools, type Node } from '../api'
import { formatAtomList, measure, parseAtomList, parseXyz } from '../chem'
import { heldBefore, readConstraints, type Held } from '../localJobs'
import { Modal } from './Modal'
import { RefineCore, type CoreChoice } from './RefineCore'
import { errorText } from '../util'

/** What each method is for (D213); the server says which are offered. */
const HELP: Record<string, string> = {
  gfn2: 'A quick clean-up before a DFT optimization on the cluster.',
  gxtb: 'g-xTB: closer to DFT than GFN2-xTB, at a few times its cost (a development version).',
  'b97-3c': 'The whole molecule at DFT quality, its steric pocket included.',
  'r2scan-3c': 'A little better than B97-3c, at about 1.4 times its cost.',
  oniom:
    'r2SCAN-3c on the metal and the atoms around it, GFN2-xTB on the rest: the core at DFT quality in a fraction of the time.',
  'oniom-stub': 'A layered stand-in for tests.',
  stub: 'A stand-in for tests.',
}

const UNITS = { 2: 'Å', 3: '°', 4: '°' } as const

function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(seconds < 36_000 ? 1 : 0)} h`
}

/** D213, FR-LOC-02: start a geometry optimization of a node on this computer, with a time
 * estimate per method and optional constraints (frozen atoms; distances, angles and dihedrals
 * at their current value or a target). The result is imported onto the node when it finishes
 * (a node without calculations takes its geometry; on one with calculations it goes to a
 * derived node, D23). */
export function RefineDialog({
  node,
  onClose,
  onStarted,
  onSettings,
}: {
  node: Node
  onClose: () => void
  onStarted: (job: LocalJob) => void
  onSettings: () => void
}) {
  const [tools, setTools] = useState<LocalTools | null>(null)
  const [method, setMethod] = useState('gfn2')
  const [charge, setCharge] = useState(String(node.charge ?? 0))
  const [multiplicity, setMultiplicity] = useState(String(node.multiplicity ?? 1))
  const [frozen, setFrozen] = useState('')
  const [held, setHeld] = useState<Held[]>([])
  const [prefilled, setPrefilled] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [core, setCore] = useState<CoreChoice | null>(null)
  const chooseCore = useCallback((choice: CoreChoice) => setCore(choice), [])
  const [transition, setTransition] = useState(false)
  const [reacting, setReacting] = useState('')
  const [fromMode, setFromMode] = useState(false)
  const atoms = useMemo(() => (node.xyz ? parseXyz(node.xyz) : []), [node.xyz])

  useEffect(() => {
    Promise.all([api.localTools(node.atom_count), api.localCore({ node_id: node.id }).catch(() => null)]).then(
      ([found, core]) => {
        setTools(found)
        // A143: a structure with a metal starts on Quick DFT (ONIOM), the rest on GFN2-xTB.
        if (core?.core.length && found.methods.some((m) => m.value === 'oniom'))
          setMethod((m) => (m === 'gfn2' ? 'oniom' : m))
        // A145: a node with an imaginary mode starts as a TS search on the atoms it moves.
        if (core?.mode_atoms.length) {
          setTransition(true)
          setFromMode(true)
          setReacting((current) => current || formatAtomList(core.mode_atoms.map((a) => a + 1)))
        }
      },
      (err: unknown) => setError(errorText(err)),
    )
    // D165: a node with a constrained optimization offers what it held.
    api.calculations(node.id).then(
      (calcs) => {
        const held = calcs.filter((c) => c.constraints?.length).at(-1)
        if (!held?.constraints) return
        const coordinates = heldBefore(held.constraints)
        setHeld((current) => (current.length ? current : coordinates))
        if (coordinates.length || held.constraints.some((c) => /frozen atom/.test(c)))
          setPrefilled(held.constraints.join(', '))
      },
      () => undefined,
    )
  }, [node.id, node.atom_count])

  const chargeValue = Number(charge)
  const multiplicityValue = Number(multiplicity)
  const stateValid =
    Number.isInteger(chargeValue) && charge.trim() !== '' && Number.isInteger(multiplicityValue) && multiplicityValue >= 1

  const constraints = readConstraints(frozen, held, atoms.length)
  const reactingRead = useMemo((): { atoms: number[] | null } | { error: string } => {
    if (!transition || !reacting.trim()) return { atoms: null }
    const found = parseAtomList(reacting)
    if ('error' in found) return { error: `Reacting atoms: ${found.error}` }
    if (!found.numbers.every((n) => n >= 1 && n <= atoms.length)) return { error: `Reacting atoms are numbered 1 to ${atoms.length}` }
    return { atoms: found.numbers.map((n) => n - 1) }
  }, [transition, reacting, atoms.length])

  const chosen = tools?.methods.find((m) => m.value === method)
  const layered = Boolean(chosen?.layered)
  const ready =
    Boolean(tools?.installed) && node.atom_count > 0 && (!layered || Boolean(core?.usable)) && !('error' in reactingRead)
  // D215: a TS search computes two GFN2-xTB Hessians, 6 gradients per atom each.
  const gfn2Step = tools?.methods.find((m) => m.value === 'gfn2')?.estimate?.seconds_per_step
  const hessians = gfn2Step ? 12 * node.atom_count * gfn2Step : null
  // D214: a layered method's time depends on its core.
  const estimateOf = (m: LocalTools['methods'][number]) => (m.layered && core?.check ? core.check.estimate : m.estimate)
  const chosenEstimate = chosen ? estimateOf(chosen) : null

  const start = () => {
    if ('error' in constraints) return
    setBusy(true)
    setError(null)
    api
      .startRefine({
        node_id: node.id,
        method,
        charge: chargeValue,
        multiplicity: multiplicityValue,
        constraints: constraints.value,
        core: layered ? core?.atoms : null,
        transition,
        reacting: 'atoms' in reactingRead ? reactingRead.atoms : null,
      })
      .then(
        (job) => {
          onStarted(job)
          onClose()
        },
        (err: unknown) => setError(errorText(err)),
      )
      .finally(() => setBusy(false))
  }

  const current = (text: string): string | null => {
    const read = parseAtomList(text)
    if ('error' in read || read.numbers.some((n) => n < 1 || n > atoms.length)) return null
    const found = measure(read.numbers.map((n) => atoms[n - 1]))
    if (!found) return null
    return `${found.value.toFixed(found.kind === 'distance' ? 3 : 1)} ${UNITS[read.numbers.length as 2 | 3 | 4]}`
  }

  return (
    <Modal
      title={`Refine geometry of “${node.label}”`}
      onClose={onClose}
      note={
        tools?.installed
          ? `${tools.cores} core${tools.cores === 1 ? '' : 's'}, ${Math.round(tools.memory_mb / 100) / 10} GB on this computer`
          : undefined
      }
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button
            className="primary"
            disabled={!ready || !stateValid || 'error' in constraints || busy}
            onClick={start}
          >
            {busy ? 'Starting…' : 'Start'}
          </button>
        </>
      }
    >
      {tools && !tools.installed && (
        <p className="notice warn">
          The tools for local calculations are not installed yet.{' '}
          <button className="link" onClick={onSettings}>
            Install them in Settings
          </button>
        </p>
      )}
      {node.atom_count === 0 && <p className="notice warn">This node has no coordinates.</p>}
      <div className="fields">
        <label className="field">
          <span>Method</span>
          <select aria-label="Method" value={method} onChange={(event) => setMethod(event.target.value)}>
            {(tools?.methods ?? [{ value: 'gfn2', label: 'GFN2-xTB', layered: false, estimate: null }]).map((m) => (
              <option key={m.value} value={m.value}>
                {estimateOf(m) ? `${m.label} · about ${duration(estimateOf(m)!.seconds)}` : m.label}
              </option>
            ))}
          </select>
          <small className="muted" aria-label="Method help">
            {HELP[method]}
            {chosenEstimate &&
              ` About ${chosenEstimate.steps} steps of ${duration(chosenEstimate.seconds_per_step)} for ${node.atom_count} atoms` +
                (chosenEstimate.measured ? ', as measured on this computer.' : ' (a first guess until a job has run here).')}
          </small>
        </label>
        <div className="field-pair">
          <label className="field">
            <span>Charge</span>
            <input aria-label="Charge" type="number" value={charge} onChange={(event) => setCharge(event.target.value)} />
          </label>
          <label className="field">
            <span>Multiplicity</span>
            <input
              aria-label="Multiplicity"
              type="number"
              min={1}
              value={multiplicity}
              onChange={(event) => setMultiplicity(event.target.value)}
            />
          </label>
        </div>
        <div className="refine-ts">
          <label className="check">
            <input
              type="checkbox"
              aria-label="Search for a transition state"
              checked={transition}
              onChange={(event) => setTransition(event.target.checked)}
            />
            Search for a transition state
          </label>
          {transition && (
            <>
              <p className="muted small">
                The search climbs to a saddle point, starting from a GFN2-xTB Hessian of the whole molecule; a second one at
                the result checks its imaginary frequency{hessians ? ` (both about ${duration(hessians)})` : ''}.
              </p>
              <label className="field">
                <span>Reacting atoms (optional)</span>
                <input
                  aria-label="Reacting atoms"
                  placeholder="e.g. 1 5 9"
                  value={reacting}
                  onChange={(event) => setReacting(event.target.value)}
                />
                <small className="muted">
                  {fromMode
                    ? 'The atoms the node’s imaginary mode moves most. The check says whether the final mode stays on them.'
                    : 'The check says whether the final imaginary mode stays on them.'}
                </small>
              </label>
              {'error' in reactingRead && (
                <p className="small cloud-job-problem" role="alert">
                  {reactingRead.error}
                </p>
              )}
            </>
          )}
        </div>
        {layered && node.xyz && (
          <RefineCore
            nodeId={node.id}
            xyz={node.xyz}
            count={atoms.length}
            charge={stateValid ? chargeValue : null}
            multiplicity={stateValid ? multiplicityValue : null}
            onChange={chooseCore}
          />
        )}
        <fieldset className="refine-constraints">
          <legend>Constraints</legend>
          {prefilled && <p className="muted small">An earlier optimization on this node held {prefilled}.</p>}
          <label className="field">
            <span>Frozen atoms</span>
            <input
              aria-label="Frozen atoms"
              placeholder="e.g. 1-3, 7"
              value={frozen}
              onChange={(event) => setFrozen(event.target.value)}
            />
          </label>
          {held.map((item, n) => (
            <div key={n} className="refine-held" role="group" aria-label={`Held coordinate ${n + 1}`}>
              <label className="field">
                <span>Atoms (2 to 4)</span>
                <input
                  aria-label="Atoms"
                  placeholder="e.g. 1 2"
                  value={item.atoms}
                  onChange={(event) => setHeld(held.map((h, k) => (k === n ? { ...h, atoms: event.target.value } : h)))}
                />
              </label>
              <label className="field">
                <span>Target</span>
                <input
                  aria-label="Target"
                  placeholder={current(item.atoms) ?? 'current'}
                  value={item.value}
                  onChange={(event) => setHeld(held.map((h, k) => (k === n ? { ...h, value: event.target.value } : h)))}
                />
              </label>
              <button className="small" aria-label="Remove this coordinate" onClick={() => setHeld(held.filter((_, k) => k !== n))}>
                ✕
              </button>
            </div>
          ))}
          <button className="small" onClick={() => setHeld([...held, { atoms: '', value: '' }])}>
            Hold a distance, angle or dihedral
          </button>
          <p className="muted small">
            Atoms are numbered from 1 as in the 3D view. An empty target holds the current value.
          </p>
          {'error' in constraints && (
            <p className="small cloud-job-problem" role="alert">
              {constraints.error}
            </p>
          )}
        </fieldset>
        <p className="muted small">
          {node.atom_count} atoms. The job runs on this computer as a program of its own and goes on when the app is
          closed. Its result is imported onto this node when it is done
          {node.calculation_count > 0 ? '; since the node has calculations, it may go to a derived node' : ''}.
        </p>
      </div>
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}

