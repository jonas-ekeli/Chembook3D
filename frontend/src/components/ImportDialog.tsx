import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  CALCULATION_TYPES,
  LARGE_FILE,
  ROLES,
  STATUSES,
  type EnsemblePlan,
  type FolderListing,
  type ImportOptions,
  type ImportPlan,
  type Node,
  type NameRequest,
} from '../api'
import { Modal } from './Modal'
import { Viewer3D } from './Viewer3D'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const DESTINATIONS: Record<string, string> = {
  node: 'Imported',
  derived: 'Derived node',
  preview: 'Not imported',
}

/** The file browser's name filter (D78): plain text matches names containing it; with * (any
 * run of characters) or ? (one character) it must match the whole name, as in a Linux shell.
 * Case is ignored either way. */
function nameFilter(text: string): (name: string) => boolean {
  const needle = text.trim().toLowerCase()
  if (!/[*?]/.test(needle)) return (name) => name.toLowerCase().includes(needle)
  const pattern = needle
    .split('')
    .map((c) => (c === '*' ? '.*' : c === '?' ? '.' : c.replace(/[.+^${}()|[\]\\]/g, '\\$&')))
    .join('')
  const glob = new RegExp(`^${pattern}$`, 's')
  return (name) => glob.test(name.toLowerCase())
}

/** Browse the local disk through the backend (a browser page cannot read paths itself), so
 * the file's own path is kept as its origin path (FR-FILE-02). It starts in the folder a file
 * was last picked from, and the filter shows only names containing its text (D78). */
function FileBrowser({
  onPick,
  onPickFolder,
}: {
  onPick: (path: string) => void
  /** D97: import every output in the folder shown. */
  onPickFolder?: (path: string) => void
}) {
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [path, setPath] = useState('')
  const [filter, setFilter] = useState('')
  const [error, setError] = useState<string | null>(null)

  const go = useCallback((target?: string) => {
    api.folders(target, true).then(
      (result) => {
        setListing(result)
        setPath(result.path)
        setFilter('')
        setError(null)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }, [])

  useEffect(() => {
    api.settings().then(
      (settings) => go(settings.last_import_folder || undefined),
      () => go(),
    )
  }, [go])

  const needle = filter.trim()
  const matches = nameFilter(needle)
  const entries = listing?.entries.filter((entry) => matches(entry.name)) ?? []

  return (
    <div className="file-browser">
      <div className="path-row">
        <input aria-label="Folder path" value={path} onChange={(event) => setPath(event.target.value)} />
        <button onClick={() => go(path)}>Go</button>
        {listing?.parent && <button onClick={() => go(listing.parent!)}>Up</button>}
        {onPickFolder && listing && (
          <button onClick={() => onPickFolder(listing.path)} title="Read every output in this folder and its subfolders">
            Import this folder…
          </button>
        )}
      </div>
      <input
        type="search"
        aria-label="Filter by name"
        placeholder="Filter by name, e.g. SPQZ or *SPQZ*.out"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        onKeyDown={(event) => {
          // Escape clears the filter first rather than closing the dialog.
          if (event.key === 'Escape' && filter) {
            event.stopPropagation()
            setFilter('')
          }
        }}
      />
      {error && <p role="alert">{error}</p>}
      <ul className="folder-list" aria-label="Files">
        {entries.map((entry) => (
          <li key={entry.path}>
            {entry.is_file ? (
              <button className="folder file-entry" onClick={() => onPick(entry.path)}>
                <span>📄 {entry.name}</span>
                <span className="muted">{entry.size !== null ? formatSize(entry.size) : ''}</span>
              </button>
            ) : (
              <button className="folder" onClick={() => go(entry.path)}>
                📁 {entry.name}
              </button>
            )}
          </li>
        ))}
        {listing && needle && entries.length === 0 && <li className="muted">No names here match “{needle}”.</li>}
      </ul>
    </div>
  )
}

export function NameField({
  request,
  value,
  onCommit,
}: {
  request: NameRequest
  value: string
  onCommit: (name: string) => void
}) {
  const [draft, setDraft] = useState(value)
  const what = request.kind === 'basis' ? 'basis set' : 'dispersion'
  if (request.recognised) {
    return (
      <p>
        {request.description}: recognised as <strong>{request.name}</strong>.
      </p>
    )
  }
  return (
    <label className="field">
      <span>
        Name for the custom {what}
        {request.elements.length > 0 && <> ({request.elements.join(', ')})</>}
      </span>
      <input
        aria-label={`Name for the custom ${what}`}
        value={draft}
        placeholder={request.kind === 'basis' ? 'e.g. modDZ' : 'e.g. GD3MBJ'}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && draft !== value) onCommit(draft)
        }}
      />
      <small className="muted">
        {request.description}
        {request.title && <> · job title: “{request.title}”</>}. Later imports with the same
        definition reuse this name.
      </small>
    </label>
  )
}

function optionalInteger(text: string): number | null {
  const value = Number.parseInt(text, 10)
  return text.trim() === '' || Number.isNaN(value) ? null : value
}

/** WF-05, FR-IMP-10: a CREST ensemble lists its conformers by energy; the lowest N are ticked
 * and any can be unticked. The file gives no method, charge or multiplicity, so they are asked
 * for here (A16). */
function EnsemblePreview({
  ensemble,
  energyUnit,
  energyFactor,
  onChange,
}: {
  ensemble: EnsemblePlan
  energyUnit: string
  energyFactor: number
  onChange: (next: Partial<ImportOptions>) => void
}) {
  const [count, setCount] = useState(String(ensemble.count))
  const selected = ensemble.conformers.filter((c) => c.selected).map((c) => c.index)
  const toggle = (index: number, on: boolean) =>
    onChange({ conformers: on ? [...selected, index] : selected.filter((i) => i !== index) })
  const applyCount = () => {
    const value = optionalInteger(count)
    if (value !== null && value >= 0) onChange({ conformer_count: value, conformers: null })
  }
  return (
    <section aria-label="Conformers">
      <p className="plan-mode">
        Creates a group with one node per ticked conformer ({ensemble.selected_count} of {ensemble.conformers.length},{' '}
        {ensemble.atom_count} atoms each). No representative is chosen; you pick one later.
      </p>
      <div className="inspector-grid">
        <div className="column fields">
          <label className="field">
            <span>Keep the lowest</span>
            <span className="row">
              <input
                aria-label="Number of conformers to keep"
                type="number"
                min={0}
                value={count}
                onChange={(event) => setCount(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && applyCount()}
              />
              <button onClick={applyCount}>Tick</button>
            </span>
          </label>
          <label className="field">
            <span>Method of the energies</span>
            <input
              aria-label="Method of the energies"
              defaultValue={ensemble.method}
              onBlur={(event) => event.target.value !== ensemble.method && onChange({ method: event.target.value })}
            />
          </label>
          <small className="muted">Stored as the level “{ensemble.level_label}”.</small>
        </div>
        <div className="column fields">
          <label className="field">
            <span>Charge</span>
            <input
              aria-label="Charge"
              type="number"
              defaultValue={ensemble.charge ?? ''}
              onBlur={(event) => onChange({ charge: optionalInteger(event.target.value) })}
            />
          </label>
          <label className="field">
            <span>Multiplicity</span>
            <input
              aria-label="Multiplicity"
              type="number"
              min={1}
              defaultValue={ensemble.multiplicity ?? ''}
              onBlur={(event) => onChange({ multiplicity: optionalInteger(event.target.value) })}
            />
          </label>
        </div>
      </div>
      <div className="conformer-list">
        <table className="steps" aria-label="Conformers">
          <thead>
            <tr>
              <th>Keep</th>
              <th>Conformer</th>
              <th>E / Eh</th>
              <th>ΔE / {energyUnit}</th>
            </tr>
          </thead>
          <tbody>
            {ensemble.conformers.map((c) => (
              <tr key={c.index} className={c.selected ? '' : 'muted'}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Keep conformer ${c.index}`}
                    checked={c.selected}
                    onChange={(event) => toggle(c.index, event.target.checked)}
                  />
                </td>
                <td>{c.index}</td>
                <td className="mono">{c.energy.toFixed(8)}</td>
                <td className="mono">{(c.relative * energyFactor).toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Import preview (FR-IMP-05, WF-04): everything that was parsed and what would be written.
 * Nothing is stored until Import is pressed; Cancel discards the staged file. */
export function ImportDialog({
  file,
  target,
  nodes,
  energyUnit,
  energyFactor,
  queued,
  position = null,
  asSpecies = false,
  linked = false,
  onClose,
  onImported,
  onImportFolder,
}: {
  file: File | null
  target: Node | null
  nodes: Node[]
  /** Start with "free species" ticked (D69). */
  asSpecies?: boolean
  energyUnit: string
  /** hartree → the energy unit */
  energyFactor: number
  queued: number
  /** Where a new node goes on the canvas (the drop point), WF-04. */
  position?: { x: number; y: number } | null
  /** The investigation syncs through Git, whose host refuses large files (FR-SYNC-09). */
  linked?: boolean
  onClose: () => void
  onImported: (imported: { kind: 'node' | 'group'; id: string }, notice: string) => void
  /** D97: switch to importing a whole folder (offered when importing new nodes). */
  onImportFolder?: (folder: string) => void
}) {
  const [plan, setPlan] = useState<ImportPlan | null>(null)
  const [options, setOptions] = useState<ImportOptions>({
    target_node_id: target?.id ?? null,
    kind: asSpecies ? 'species' : null,
    pos_x: position?.x ?? null,
    pos_y: position?.y ?? null,
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const token = useRef<string | null>(null)
  const started = useRef(false)

  const staged = (result: ImportPlan) => {
    token.current = result.token
    setPlan(result)
    setError(null)
  }

  useEffect(() => {
    if (!file || started.current) return
    started.current = true
    setBusy(true)
    api
      .uploadImport(file, file.name, target?.id)
      .then(staged, (err: unknown) => setError(errorText(err)))
      .finally(() => setBusy(false))
  }, [file, target])

  const fromPath = (path: string) => {
    setBusy(true)
    api
      .importFromPath(path, target?.id)
      .then(staged, (err: unknown) => setError(errorText(err)))
      .finally(() => setBusy(false))
  }

  // Answers to quick successive changes (two names typed in a row) can arrive out of order;
  // only the latest request's answer may replace the preview.
  const previewRequest = useRef(0)
  const change = (next: Partial<ImportOptions>) => {
    const merged = { ...options, ...next }
    setOptions(merged)
    if (!plan) return
    const request = ++previewRequest.current
    api.previewImport(plan.token, merged).then(
      (result) => request === previewRequest.current && staged(result),
      (err: unknown) => request === previewRequest.current && setError(errorText(err)),
    )
  }

  const cancel = () => {
    if (token.current) void api.cancelImport(token.current)
    onClose()
  }

  const commit = () => {
    if (!plan) return
    setBusy(true)
    api.commitImport(plan.token, options).then(
      (result) => {
        token.current = null
        if (result.group_id) {
          const count = result.calculation_ids.length
          onImported(
            { kind: 'group', id: result.group_id },
            `Imported ${count} conformer${count === 1 ? '' : 's'} from ${plan.original_name} as a group.`,
          )
          return
        }
        const parts = [`Imported ${result.calculation_ids.length} calculation(s) from ${plan.original_name}.`]
        if (result.derived_node_id) parts.push('A derived node was created for the new geometry.')
        onImported({ kind: 'node', id: result.derived_node_id ?? result.node_id! }, parts.join(' '))
      },
      (err: unknown) => {
        setBusy(false)
        setError(err instanceof ApiError && err.blockers ? err.blockers.join('; ') : errorText(err))
      },
    )
  }

  const species = options.kind === 'species'
  const title = target
    ? `Import onto “${target.label || 'Untitled node'}”`
    : asSpecies
      ? 'Import a free species'
      : 'Import calculation output'
  const chosen = plan?.steps.find((s) => s.index === plan.chosen_step) ?? null
  const writesNode = plan && (plan.mode !== 'onto' || plan.derived_offered)
  const nodeLabel = (id: string) => nodes.find((n) => n.id === id)?.label || 'Untitled node'

  return (
    <Modal
      title={title + (queued > 0 ? ` (${queued} more file${queued === 1 ? '' : 's'} waiting)` : '')}
      onClose={cancel}
      wide
      actions={
        <>
          <button onClick={cancel}>Cancel</button>
          <button className="primary" disabled={!plan || busy || plan.blockers.length > 0} onClick={commit}>
            Import
          </button>
        </>
      }
    >
      {error && <p role="alert">{error}</p>}
      {!plan && !file && (
        <>
          <p>Choose a Gaussian, ORCA or xTB output file, or a CREST conformer ensemble (.xyz).</p>
          <label className="button">
            Upload a file…
            <input
              type="file"
              aria-label="Upload a file"
              hidden
              onChange={(event) => {
                const picked = event.target.files?.[0]
                if (!picked) return
                setBusy(true)
                api
                  .uploadImport(picked, picked.name, target?.id)
                  .then(staged, (err: unknown) => setError(errorText(err)))
                  .finally(() => setBusy(false))
              }}
            />
          </label>
          <p className="muted">
            Or pick it from this computer, which also records where it came from
            {onImportFolder && ', or import every output in a folder at once'}:
          </p>
          <FileBrowser
            onPick={fromPath}
            onPickFolder={
              onImportFolder &&
              ((folder) => {
                if (token.current) void api.cancelImport(token.current)
                onImportFolder(folder)
              })
            }
          />
        </>
      )}
      {!plan && busy && <p className="muted">Reading the file…</p>}
      {plan && (
        <div className="import-preview" aria-label="Import preview">
          <p>
            <strong>{plan.original_name}</strong> · {plan.program} {plan.program_version ?? ''} ·{' '}
            {formatSize(plan.size)} ·{' '}
            {plan.ensemble
              ? `${plan.ensemble.conformers.length} conformers`
              : `${plan.steps.length} job step${plan.steps.length === 1 ? '' : 's'}`}
          </p>
          {linked && plan.size > LARGE_FILE && (
            <p className="notice warn" role="status">
              This file is larger than 100 MB. GitHub refuses files that large, so this investigation cannot be pushed
              once it is imported. Consider keeping only the relevant part of the output.
            </p>
          )}
          {plan.already_imported.map((entry) => (
            <p key={entry.imported_at} className="notice" role="status">
              This file was already imported as “{entry.original_name}” on{' '}
              {new Date(entry.imported_at + 'Z').toLocaleString()}
              {entry.nodes.length > 0 && <> (node {entry.nodes.join(', ')})</>}. You can import it again.
            </p>
          ))}

          {plan.ensemble && (
            <EnsemblePreview
              ensemble={plan.ensemble}
              energyUnit={energyUnit}
              energyFactor={energyFactor}
              onChange={change}
            />
          )}

          {!plan.ensemble && (
            <>
              <p className="plan-mode">
                {plan.mode === 'new' &&
                  (species ? 'Creates a new free species (not drawn on the canvas).' : 'Creates a new node.')}
                {plan.mode === 'planned' &&
                  `Replaces the guess geometry of “${plan.target_label || 'Untitled node'}”; the old coordinates stay in its history.`}
                {plan.mode === 'onto' && `Adds calculations to “${plan.target_label || 'Untitled node'}”.`}
              </p>

              {plan.duplicates.length > 0 && (
                <fieldset className="choice">
                  <legend>Possible duplicate</legend>
                  {plan.duplicates.map((d) => (
                    <label key={d.node_id}>
                      <input
                        type="radio"
                        name="duplicate"
                        checked={options.duplicate_action === 'attach' && options.duplicate_node_id === d.node_id}
                        onChange={() => change({ duplicate_action: 'attach', duplicate_node_id: d.node_id })}
                      />
                      Attach to “{nodeLabel(d.node_id)}” (RMSD {d.rmsd.toFixed(3)} Å)
                    </label>
                  ))}
                  <label>
                    <input
                      type="radio"
                      name="duplicate"
                      checked={options.duplicate_action === 'new'}
                      onChange={() => change({ duplicate_action: 'new', duplicate_node_id: null })}
                    />
                    Create a new node
                  </label>
                </fieldset>
              )}

              <table className="steps" aria-label="Job steps">
                <thead>
                  <tr>
                    <th>Geometry</th>
                    <th>Step</th>
                    <th>Type</th>
                    <th>Level of theory</th>
                    <th>E(SCF) / Eh</th>
                    <th>Termination</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.steps.map((step) => (
                    <tr key={step.index} className={step.assignment === 'preview' ? 'muted' : ''}>
                      <td>
                        <input
                          type="radio"
                          name="chosen-step"
                          aria-label={`Use the geometry of step ${step.index}`}
                          disabled={step.atom_count === 0}
                          checked={plan.chosen_step === step.index}
                          onChange={() => change({ step: step.index })}
                        />
                      </td>
                      <td>{step.index}</td>
                      <td>
                        {CALCULATION_TYPES[step.type] ?? step.type}
                        {step.optimization_converged === false && <span className="badge warn">not converged</span>}
                      </td>
                      <td>
                        {step.level_label}
                        {step.geometry_level_label && <> // {step.geometry_level_label}</>}
                      </td>
                      <td className="mono">{step.energy?.toFixed(8) ?? '—'}</td>
                      <td>{step.termination === 'normal' ? 'Normal' : <span className="badge warn">Abnormal</span>}</td>
                      <td>{DESTINATIONS[step.assignment]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                The node geometry is the last geometry of the chosen step (by default the last step).
                Steps are imported when their geometry matches that node; the others are only shown here.
              </p>

              {plan.derived_offered && (
                <label className="check">
                  <input
                    type="checkbox"
                    checked={options.create_derived ?? true}
                    onChange={(event) => change({ create_derived: event.target.checked })}
                  />
                  Create a derived node for the steps on the new geometry
                </label>
              )}

              {plan.names.length > 0 && (
                <section aria-label="Custom names">
                  <h3>Custom basis sets and dispersion</h3>
                  {plan.names.map((request) => (
                    <NameField
                      key={request.key}
                      request={request}
                      value={
                        (request.kind === 'basis' ? options.basis_names : options.dispersion_names)?.[request.key] ?? ''
                      }
                      onCommit={(name) =>
                        change(
                          request.kind === 'basis'
                            ? { basis_names: { ...options.basis_names, [request.key]: name } }
                            : { dispersion_names: { ...options.dispersion_names, [request.key]: name } },
                        )
                      }
                    />
                  ))}
                </section>
              )}
            </>
          )}

          <div className="inspector-grid">
            <div className="column">
              {writesNode && (
                <section className="fields" aria-label="New node">
                  <h3>
                    {plan.ensemble
                      ? 'Group'
                      : plan.mode === 'onto'
                        ? target?.kind === 'species'
                          ? 'Derived species'
                          : 'Derived node'
                        : species
                          ? 'Free species'
                          : 'Node'}
                  </h3>
                  {plan.mode === 'new' && !plan.ensemble && (
                    <label className="check" title="A substrate or fragment that joins or leaves on a transition (D69)">
                      <input
                        type="checkbox"
                        checked={species}
                        onChange={(event) => change({ kind: event.target.checked ? 'species' : null })}
                      />
                      <span>Free species (substrate or fragment, kept off the canvas)</span>
                    </label>
                  )}
                  {plan.ensemble && (
                    <small className="muted">The members are named after it, with the conformer's number.</small>
                  )}
                  <label className="field">
                    <span>Label</span>
                    <input
                      aria-label="Node label"
                      defaultValue={plan.suggested.label}
                      onBlur={(event) => change({ label: event.target.value })}
                    />
                  </label>
                  <label className="field">
                    <span>Role</span>
                    <select
                      aria-label="Node role"
                      value={options.role ?? plan.suggested.role}
                      onChange={(event) => change({ role: event.target.value as ImportOptions['role'] })}
                    >
                      {ROLES.map((r) => (
                        <option key={r.value} value={r.value}>
                          {r.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    <span>Status</span>
                    <select
                      aria-label="Node status"
                      value={options.status ?? plan.suggested.status}
                      onChange={(event) => change({ status: event.target.value as ImportOptions['status'] })}
                    >
                      {STATUSES.map((s) => (
                        <option key={s.value} value={s.value}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </section>
              )}

              {chosen && (
                <section aria-label="Parsed values">
                  <h3>Step {chosen.index} values</h3>
                  <dl className="values">
                    <dt>Charge, multiplicity</dt>
                    <dd>
                      {chosen.charge ?? '—'}, {chosen.multiplicity ?? '—'}
                    </dd>
                    <dt>Atoms</dt>
                    <dd>{chosen.atom_count}</dd>
                    <dt>Geometries in step</dt>
                    <dd>{chosen.geometry_count}</dd>
                    {chosen.thermo.zpe !== undefined && (
                      <>
                        <dt>ZPE</dt>
                        <dd className="mono">{chosen.thermo.zpe} Eh</dd>
                        <dt>H</dt>
                        <dd className="mono">{chosen.thermo.h} Eh</dd>
                        <dt>G</dt>
                        <dd className="mono">{chosen.thermo.g} Eh</dd>
                        <dt>T, P</dt>
                        <dd>
                          {chosen.thermo.temperature} K, {chosen.thermo.pressure} atm
                        </dd>
                      </>
                    )}
                    {chosen.frequency_count > 0 && (
                      <>
                        <dt>Frequencies</dt>
                        <dd>
                          {chosen.frequency_count}, {chosen.imaginary_count} imaginary; lowest{' '}
                          {chosen.lowest_frequencies.map((f) => f.toFixed(1)).join(', ')} cm⁻¹
                        </dd>
                      </>
                    )}
                  </dl>
                  <p className="muted small">
                    Energies are shown in hartree as printed; the {energyUnit} setting applies to
                    relative energies.
                  </p>
                </section>
              )}

              {plan.warnings.length > 0 && (
                <section aria-label="Import warnings">
                  <h3>Warnings</h3>
                  <ul className="warnings">
                    {plan.warnings.map((w, i) => (
                      <li key={i}>
                        <span className="badge warn">{w.code}</span> {w.message}
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              <section className="fields" aria-label="Origin">
                <h3>Where the file came from</h3>
                <label className="field">
                  <span>Device or server</span>
                  <input
                    aria-label="Origin device"
                    defaultValue={plan.origin.device}
                    placeholder="e.g. the cluster's name"
                    onBlur={(event) => change({ origin_device: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Path on it</span>
                  <input
                    aria-label="Origin path"
                    defaultValue={plan.origin.path}
                    onBlur={(event) => change({ origin_path: event.target.value })}
                  />
                </label>
                <label className="field">
                  <span>File name</span>
                  <input
                    aria-label="Origin file name"
                    defaultValue={plan.origin.name}
                    onBlur={(event) => change({ original_name: event.target.value })}
                  />
                </label>
              </section>
            </div>
            <div className="column">
              <Viewer3D models={plan.xyz ? [{ xyz: plan.xyz }] : []} />
            </div>
          </div>

          {plan.blockers.length > 0 && (
            <ul className="blockers" aria-label="Needed before import">
              {plan.blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Modal>
  )
}
