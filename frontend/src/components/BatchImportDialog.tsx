import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  CALCULATION_TYPES,
  LARGE_FILE,
  type BatchImported,
  type BatchOptions,
  type BatchPlan,
  type BatchRow,
  type BatchRowChoice,
  type Node,
} from '../api'
import { NameField } from './ImportDialog'
import { Modal } from './Modal'

function errorText(err: unknown): string {
  if (err instanceof ApiError && err.blockers) return err.blockers.join('; ')
  return err instanceof Error ? err.message : String(err)
}

/** How a file's target was found (D97). */
const MATCHES: Record<BatchRow['match'], string> = {
  geometry: 'same geometry',
  first_geometry: 'started from its guess',
  name: 'by name, please check',
  name_close: 'by a close name, please check',
  new: 'nothing matched',
  chosen: 'your choice',
  duplicate: 'possible duplicate, attached',
  none: 'needs your choice',
}

function where(row: BatchRow): string {
  const target = row.target
  if (!target) return '—'
  const name = target.kind === 'file' ? `the node from ${target.file}` : `“${target.label || 'Untitled node'}”`
  if (target.kind === 'group') return `New group “${target.label}”`
  if (target.kind === 'new') return `New node “${target.label}”`
  if (row.mode === 'planned') return `Finishes ${name}`
  return `Adds to ${name}${row.derived ? ', with a derived node for its other geometry' : ''}`
}

function RowDetails({ row }: { row: BatchRow }) {
  return (
    <div className="batch-details">
      {row.skipped_detail && <p className="muted">{row.skipped_detail}.</p>}
      {row.steps.length > 0 && (
        <table className="steps" aria-label={`Job steps of ${row.path}`}>
          <thead>
            <tr>
              <th>Step</th>
              <th>Type</th>
              <th>Level of theory</th>
              <th>E(SCF) / Eh</th>
              <th>Termination</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {row.steps.map((step) => (
              <tr key={step.index} className={step.assignment === 'preview' ? 'muted' : ''}>
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
                <td>
                  {step.assignment === 'node' ? 'Imported' : step.assignment === 'derived' ? 'Derived node' : 'Not imported'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {row.warnings.length > 0 && (
        <ul className="warnings">
          {row.warnings.map((w, i) => (
            <li key={i}>
              <span className="badge warn">{w.code}</span> {w.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Import a whole folder of results at once (D97, FR-IMP-14): every output is read, each gets a
 * proposed node, and Import writes all ticked files in one transaction. Nothing is stored before. */
export function BatchImportDialog({
  folder,
  nodes,
  linked = false,
  onClose,
  onImported,
}: {
  folder: string
  nodes: Node[]
  /** The investigation syncs through Git, whose host refuses large files (FR-SYNC-09). */
  linked?: boolean
  onClose: () => void
  onImported: (result: BatchImported, notice: string) => void
}) {
  const [recursive, setRecursive] = useState(true)
  const [plan, setPlan] = useState<BatchPlan | null>(null)
  const [options, setOptions] = useState<BatchOptions>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [open, setOpen] = useState<Set<string>>(new Set())
  const token = useRef<string | null>(null)
  // Only the latest request's answer may replace the table (quick successive changes).
  const latest = useRef(0)

  const shown = (request: number) => (result: BatchPlan) => {
    if (request !== latest.current) {
      if (result.token !== token.current) void api.cancelBatch(result.token)
      return
    }
    token.current = result.token
    setPlan(result)
    setError(null)
  }
  const failed = (request: number) => (err: unknown) => {
    if (request === latest.current) setError(errorText(err))
  }

  const scan = useCallback(
    (withSubfolders: boolean, keep: BatchOptions) => {
      const request = ++latest.current
      if (token.current) void api.cancelBatch(token.current)
      token.current = null
      // Rows are new after a scan; the names, computer and suffixes still apply.
      const rest: BatchOptions = { ...keep, rows: {} }
      setOptions(rest)
      setBusy(true)
      setPlan(null)
      api
        .scanFolder(folder, withSubfolders)
        .then((result) => {
          const { rows: _rows, ...chosen } = rest
          return Object.values(chosen).some((v) => v != null) ? api.previewBatch(result.token, rest) : result
        })
        .then(shown(request), failed(request))
        .finally(() => request === latest.current && setBusy(false))
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [folder],
  )

  const started = useRef(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    scan(true, {})
  }, [scan])

  const change = (next: BatchOptions) => {
    const merged = { ...options, ...next }
    setOptions(merged)
    if (!plan) return
    const request = ++latest.current
    api.previewBatch(plan.token, merged).then(shown(request), failed(request))
  }
  const choose = (row: BatchRow, choice: BatchRowChoice) =>
    change({ rows: { ...options.rows, [row.id]: { ...options.rows?.[row.id], ...choice } } })

  const cancel = () => {
    latest.current++
    if (token.current) void api.cancelBatch(token.current)
    onClose()
  }

  const commit = () => {
    if (!plan) return
    setBusy(true)
    api.commitBatch(plan.token, options).then(
      (result) => {
        token.current = null
        const made = result.files.filter((f) => f.how === 'new' || f.how === 'group' || f.how === 'derived').length
        const files = `${result.imported} file${result.imported === 1 ? '' : 's'}`
        onImported(
          result,
          `Imported ${files} from ${plan.folder}: ${made} new node${made === 1 ? '' : 's'} or group${made === 1 ? '' : 's'}, ` +
            `${result.imported - made} added to existing nodes.`,
        )
      },
      (err: unknown) => {
        setBusy(false)
        setError(errorText(err))
      },
    )
  }

  const toggleOpen = (id: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const byLabel = [...nodes].sort((a, b) => (a.label || '').localeCompare(b.label || ''))
  // Other files whose import makes a new node can be chosen as a target too.
  const makers = plan?.rows.filter((r) => r.included && r.kind === 'steps' && r.target?.kind === 'new') ?? []
  const counts = plan?.counts

  return (
    <Modal
      title="Import a folder of results"
      onClose={cancel}
      wide
      actions={
        <>
          <button onClick={cancel}>Cancel</button>
          <button
            className="primary"
            disabled={!plan || busy || plan.blockers.length > 0}
            onClick={commit}
          >
            {counts ? `Import ${counts.included} file${counts.included === 1 ? '' : 's'}` : 'Import'}
          </button>
        </>
      }
    >
      <div className="batch-import" aria-label="Folder import">
        <p>
          <code>{folder}</code>
        </p>
        <label className="check">
          <input
            type="checkbox"
            checked={recursive}
            onChange={(event) => {
              setRecursive(event.target.checked)
              scan(event.target.checked, options)
            }}
          />
          Include subfolders
        </label>
        {error && <p role="alert">{error}</p>}
        {busy && !plan && <p className="muted">Reading the files…</p>}
        {plan && counts && (
          <>
            <p className="plan-mode" role="status">
              {counts.files} output file{counts.files === 1 ? '' : 's'}: {counts.new} new, {counts.finished} finishing a
              planned node, {counts.attached} added to a node
              {counts.skipped > 0 && <>, {counts.skipped} skipped (imported before)</>}.{' '}
              {plan.other_count > 0 && (
                <span className="muted">
                  {plan.other_count} other file{plan.other_count === 1 ? ' is' : 's are'} no output and left out.
                </span>
              )}
            </p>
            {plan.unreadable.length > 0 && (
              <details>
                <summary>
                  {plan.unreadable.length} file{plan.unreadable.length === 1 ? '' : 's'} could not be read
                </summary>
                <ul>
                  {plan.unreadable.map((u) => (
                    <li key={u.path}>
                      {u.path}: {u.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className="batch-table">
              <table className="steps" aria-label="Files">
                <thead>
                  <tr>
                    <th>Import</th>
                    <th>File</th>
                    <th>Jobs</th>
                    <th>Goes to</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.rows.map((row) => {
                    const choice = options.rows?.[row.id]
                    return (
                      <Fragment key={row.id}>
                        <tr className={row.included ? '' : 'muted'} aria-label={row.path}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={`Import ${row.path}`}
                              checked={row.included}
                              onChange={(event) => choose(row, { included: event.target.checked })}
                            />
                          </td>
                          <td>
                            <button className="link" onClick={() => toggleOpen(row.id)} aria-expanded={open.has(row.id)}>
                              {row.path}
                            </button>
                            <div className="muted small">
                              {row.program} {row.version ?? ''}
                              {row.skipped && <> · skipped: {row.skipped === 'same_file' ? 'same content as another file' : 'imported before'}</>}
                            </div>
                            {linked && row.size > LARGE_FILE && <span className="badge warn">over 100 MB</span>}
                          </td>
                          <td>
                            {row.jobs}
                            {row.termination !== 'normal' && <span className="badge warn">Abnormal</span>}
                            {row.level_label && <div className="muted small">{row.level_label}</div>}
                          </td>
                          <td>
                            {row.included && (
                              <>
                                <div>
                                  {where(row)}{' '}
                                  <span className={row.match.startsWith('name') || row.match === 'none' ? 'badge warn' : 'badge'}>
                                    {MATCHES[row.match]}
                                  </span>
                                </div>
                                {row.kind === 'steps' && (
                                  <select
                                    aria-label={`Where ${row.path} goes`}
                                    value={choice?.target ?? 'auto'}
                                    onChange={(event) => choose(row, { target: event.target.value })}
                                  >
                                    <option value="auto">Automatic</option>
                                    <option value="new">A new node</option>
                                    {makers
                                      .filter((m) => m.id !== row.id)
                                      .map((m) => (
                                        <option key={m.id} value={`file:${m.id}`}>
                                          The new node from {m.path}
                                        </option>
                                      ))}
                                    <optgroup label="Nodes">
                                      {byLabel.map((n) => (
                                        <option key={n.id} value={`node:${n.id}`}>
                                          {n.label || 'Untitled node'}
                                          {n.kind === 'species' ? ' (free species)' : ''}
                                        </option>
                                      ))}
                                    </optgroup>
                                  </select>
                                )}
                                {(row.target?.kind === 'new' || row.target?.kind === 'group') && (
                                  <input
                                    aria-label={`Label for ${row.path}`}
                                    defaultValue={row.label ?? ''}
                                    key={`${row.id}-${row.label}`}
                                    onBlur={(event) =>
                                      event.target.value !== row.label && choose(row, { label: event.target.value })
                                    }
                                  />
                                )}
                                {row.duplicates.length > 0 && (
                                  <select
                                    aria-label={`Possible duplicate of ${row.path}`}
                                    value={row.duplicate_action ?? ''}
                                    onChange={(event) =>
                                      choose(row, {
                                        duplicate_action: (event.target.value || null) as BatchRowChoice['duplicate_action'],
                                      })
                                    }
                                  >
                                    <option value="">Possible duplicate: choose…</option>
                                    <option value="attach">
                                      Attach to “{row.duplicates[0].label}” (RMSD {row.duplicates[0].rmsd.toFixed(3)} Å)
                                    </option>
                                    <option value="new">Make a new node</option>
                                  </select>
                                )}
                                {row.blockers.map((b) => (
                                  <div key={b} className="blocker-line" role="note">
                                    {b}
                                  </div>
                                ))}
                                {row.warnings.length > 0 && (
                                  <button className="link small" onClick={() => toggleOpen(row.id)}>
                                    {row.warnings.length} warning{row.warnings.length === 1 ? '' : 's'}
                                  </button>
                                )}
                              </>
                            )}
                          </td>
                        </tr>
                        {open.has(row.id) && (
                          <tr className="batch-details-row">
                            <td />
                            <td colSpan={3}>
                              <RowDetails row={row} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {plan.names.length > 0 && (
              <section aria-label="Custom names">
                <h3>Custom basis sets and dispersion</h3>
                <p className="muted small">Named once for every file in the folder that uses them.</p>
                {plan.names.map((request) => (
                  <NameField
                    key={request.key}
                    request={request}
                    value={(request.kind === 'basis' ? options.basis_names : options.dispersion_names)?.[request.key] ?? ''}
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

            <div className="inspector-grid">
              <section className="fields column" aria-label="Origin">
                <h3>Where the files came from</h3>
                <label className="field">
                  <span>Device or server</span>
                  <input
                    aria-label="Origin device"
                    defaultValue={plan.origin_device}
                    placeholder="e.g. the cluster's name"
                    onBlur={(event) =>
                      event.target.value !== plan.origin_device && change({ origin_device: event.target.value })
                    }
                  />
                </label>
                <small className="muted">Each file's own path is kept as its origin path.</small>
              </section>
              <section className="fields column" aria-label="Name matching">
                <h3>Matching file names to labels</h3>
                <label className="field">
                  <span>Suffixes left out of file names</span>
                  <input
                    aria-label="Suffixes left out of file names"
                    defaultValue={plan.suffixes.join(', ')}
                    key={plan.suffixes.join(',')}
                    onBlur={(event) => {
                      const suffixes = event.target.value
                        .split(',')
                        .map((s) => s.trim())
                        .filter(Boolean)
                      if (suffixes.join(',') !== plan.suffixes.join(',')) change({ suffixes })
                    }}
                  />
                </label>
                <small className="muted">
                  With <code>_SP*</code>, TS1-2_SPQZ.out goes to the node “TS1-2”. * stands for any characters, ? for one.
                </small>
              </section>
            </div>

            {plan.blockers.length > 0 && (
              <ul className="blockers" aria-label="Needed before import">
                {plan.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
