import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent } from 'react'
import {
  api,
  ApiError,
  formatDelta,
  geometryToXyz,
  type EditorScan,
  type JoinedPath,
  type ScanEditorState,
  type ScanEditorTarget,
  type ScanPiece,
  type Settings,
} from '../api'
import { download, fileName, hartree } from '../util'
import { FileBrowser } from './ImportDialog'
import { Modal } from './Modal'
import { EnergyChart } from './StepMovie'
import { Viewer3D } from './Viewer3D'

// D135: the scan editor puts several scans together into one path, as a short film is cut
// together from clips, without importing anything. The files' scans are listed and drawn on one
// chart against their scanned coordinate; the path is a strip of pieces, each a run of one scan's
// points in either direction, that can be reversed, trimmed, split, moved and removed. Adding a
// scan puts it on the right way round (another direction from the same structure goes in front,
// reversed), and files added together are chained where one starts where another ends. The
// backend joins the pieces again after every change, with the checks at each join (A80), and
// Save writes the joined path as an xyz file with its level and energies. Nothing is written
// anywhere until Save.

const COLOURS = [
  '#1f77b4',
  '#d62728',
  '#2ca02c',
  '#9467bd',
  '#ff7f0e',
  '#17becf',
  '#8c564b',
  '#e377c2',
  '#7f7f7f',
  '#bcbd22',
]
const SPEEDS = [1, 2, 5, 10, 20, 30]
const HARTREE_KCAL = 627.5094740631

function errorText(err: unknown): string {
  if (err instanceof ApiError && typeof err.detail === 'string') return err.detail
  return err instanceof Error ? err.message : String(err)
}

function scanName(scan: EditorScan): string {
  return scan.steps > 1 ? `${scan.file} (step ${scan.step})` : scan.file
}

function value(v: number, unit: string): string {
  return unit === 'Å' ? v.toFixed(3) : unit === '°' ? v.toFixed(1) : v.toFixed(2)
}

function range(scan: EditorScan): string {
  const first = scan.points[0]?.value ?? 0
  const last = scan.points[scan.points.length - 1]?.value ?? 0
  const points = `${scan.points.length} points`
  return `${scan.coordinate}, ${value(first, scan.unit)} → ${value(last, scan.unit)} ${scan.unit}, ${points}`
}

function sameAtoms(a: number[], b: number[]): boolean {
  const same = (x: number[], y: number[]) => x.length === y.length && x.every((v, i) => v === y[i])
  return same(a, b) || same(a, [...b].reverse())
}

/** The pieces with the one at `at` replaced by `next` (none: removed). */
function replaced(pieces: ScanPiece[], at: number, next: ScanPiece[]): ScanPiece[] {
  return [...pieces.slice(0, at), ...next, ...pieces.slice(at + 1)]
}

function swapped(pieces: ScanPiece[], a: number, b: number): ScanPiece[] {
  const next = [...pieces]
  next[a] = pieces[b]
  next[b] = pieces[a]
  return next
}

/** A scan's point shown instead of the path's frame, after a click on the chart. */
type Preview = { scan: string; index: number }

export function ScanEditor({
  targets,
  settings,
  onClose,
}: {
  /** The files to open with; more are added in the window. */
  targets: ScanEditorTarget[]
  settings: Settings | null
  onClose: () => void
}) {
  const [editor, setEditor] = useState<ScanEditorState | null>(null)
  const [path, setPath] = useState<JoinedPath | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [notes, setNotes] = useState<string[]>([])
  const [undo, setUndo] = useState<ScanPiece[][]>([])
  const [redo, setRedo] = useState<ScanPiece[][]>([])
  const [adding, setAdding] = useState(false)
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(5)
  const [zero, setZero] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [copied, setCopied] = useState<string | null>(null)
  const opened = useRef<string | null>(null)

  const pieces = useMemo(() => path?.pieces ?? [], [path])
  const scans = useMemo(() => editor?.scans ?? [], [editor])
  const byKey = useMemo(() => new Map(scans.map((s) => [s.key, s])), [scans])
  const colour = useCallback(
    (key: string) => COLOURS[Math.max(scans.findIndex((s) => s.key === key), 0) % COLOURS.length],
    [scans],
  )

  /** The path after a change, joined again by the backend; the pieces `before` it go on the
   * undo list. */
  const join = useCallback(
    async (
      id: string,
      body: { pieces: ScanPiece[]; add?: string | null; chain?: string[] | null },
      before: ScanPiece[] | null,
    ) => {
      setBusy(true)
      try {
        const joined = await api.joinScans(id, body)
        setPath(joined)
        setNotes(joined.notes)
        setError(null)
        setIndex((i) => Math.min(i, Math.max(joined.frames.length - 1, 0)))
        setZero((z) => (z < joined.frames.length ? z : 0))
        if (before) {
          setUndo((u) => [...u, before])
          setRedo([])
        }
      } catch (err) {
        setError(errorText(err))
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  useEffect(() => {
    let cancelled = false
    api.openScanEditor(targets).then(
      (found) => {
        opened.current = found.id
        if (cancelled) {
          void api.closeScanEditor(found.id)
          return
        }
        setEditor(found)
        setBusy(false)
        // Files opened together are chained where they meet (A80).
        if (found.added.length > 0) void join(found.id, { pieces: [], chain: found.added }, null)
      },
      (err: unknown) => {
        if (cancelled) return
        setError(errorText(err))
        setBusy(false)
      },
    )
    return () => {
      cancelled = true
      if (opened.current) void api.closeScanEditor(opened.current)
      opened.current = null
    }
  }, [targets, join])

  const change = useCallback(
    (next: ScanPiece[], extra: { add?: string; chain?: string[] } = {}) => {
      if (!editor) return
      setPreview(null)
      void join(editor.id, { pieces: next, ...extra }, pieces)
    },
    [editor, join, pieces],
  )

  const back = useCallback(() => {
    if (!editor || undo.length === 0) return
    const previous = undo[undo.length - 1]
    setUndo(undo.slice(0, -1))
    setRedo((r) => [...r, pieces])
    void join(editor.id, { pieces: previous }, null)
  }, [editor, undo, pieces, join])
  const forward = useCallback(() => {
    if (!editor || redo.length === 0) return
    const next = redo[redo.length - 1]
    setRedo(redo.slice(0, -1))
    setUndo((u) => [...u, pieces])
    void join(editor.id, { pieces: next }, null)
  }, [editor, redo, pieces, join])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return
      if (!(event.ctrlKey || event.metaKey)) return
      const key = event.key.toLowerCase()
      if (key === 'z' && !event.shiftKey) {
        event.preventDefault()
        back()
      } else if (key === 'y' || (key === 'z' && event.shiftKey)) {
        event.preventDefault()
        forward()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [back, forward])

  /** More files: their scans are listed and those that meet the path are joined on. */
  const addFiles = async (paths: string[]) => {
    if (!editor) return
    setBusy(true)
    try {
      const found = await api.addScanFiles(
        editor.id,
        paths.map((p) => ({ path: p })),
      )
      setEditor(found)
      setError(null)
      if (found.problems.length === 0) setAdding(false)
      if (found.added.length > 0) await join(found.id, { pieces, chain: found.added }, pieces)
      else setBusy(false)
    } catch (err) {
      setError(errorText(err))
      setBusy(false)
    }
  }

  const removeScan = async (key: string) => {
    if (!editor) return
    const next = pieces.filter((p) => p.scan !== key)
    try {
      await api.removeEditorScan(editor.id, key)
      setEditor({ ...editor, scans: editor.scans.filter((s) => s.key !== key) })
      setPreview(null)
      // Undo cannot bring back pieces of a scan that is gone.
      setUndo((u) => u.filter((ps) => ps.every((p) => p.scan !== key)))
      setRedo((r) => r.filter((ps) => ps.every((p) => p.scan !== key)))
      if (next.length !== pieces.length) void join(editor.id, { pieces: next }, null)
    } catch (err) {
      setError(errorText(err))
    }
  }

  const frames = useMemo(() => path?.frames ?? [], [path])
  const count = frames.length
  const at = Math.min(index, Math.max(count - 1, 0))
  const xyzFrames = useMemo(() => frames.map((f, n) => geometryToXyz(f.geometry, `frame ${n + 1}`)), [frames])

  useEffect(() => {
    if (!playing || count < 2) return
    const timer = setInterval(() => setIndex((i) => (Math.min(i, count - 1) + 1) % count), 1000 / speed)
    return () => clearInterval(timer)
  }, [playing, speed, count])

  const show = (i: number) => {
    setPlaying(false)
    setPreview(null)
    setIndex(i)
  }
  const move = (by: number) => show((at + by + count) % count)

  const frame = frames[at] ?? null
  const zeroEnergy = frames[zero]?.energy ?? null
  const unit = settings?.energy_unit ?? 'kcal/mol'
  const factor = settings ? (settings.energy_factors[unit] ?? HARTREE_KCAL) : HARTREE_KCAL
  const previewScan = preview ? byKey.get(preview.scan) : undefined
  const previewPoint = preview ? previewScan?.points[preview.index] : undefined

  /** D130: the structure shown as xyz text, with where it comes from and its energy. */
  const copyXyz = async () => {
    let rows = frame?.geometry
    let comment = ''
    if (previewScan && previewPoint) {
      rows = previewPoint.geometry
      comment = `${scanName(previewScan)}, point ${previewPoint.number}`
      if (previewPoint.energy !== null) comment += `, E = ${previewPoint.energy} Eh`
    } else if (frame) {
      comment = `joined scan, frame ${at + 1} of ${count}, ${frame.source}`
      if (frame.energy !== null) comment += `, E = ${frame.energy} Eh`
    }
    if (!rows) return
    try {
      await navigator.clipboard.writeText(geometryToXyz(rows, comment))
      setCopied(previewPoint ? `Copied point ${previewPoint.number}` : `Copied frame ${at + 1}`)
    } catch {
      setCopied('The browser did not allow copying')
    }
  }
  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(null), 2500)
    return () => clearTimeout(timer)
  }, [copied])

  const save = () => {
    if (!path || path.frames.length === 0) return
    const first = byKey.get(pieces[0]?.scan ?? '')
    const stem = (first?.file ?? 'scan').replace(/\.[^.]+$/, '')
    const url = URL.createObjectURL(new Blob([path.text], { type: 'chemical/x-xyz' }))
    download(url, fileName(`${stem}_joined.xyz`, 'joined_scan.xyz'))
    setTimeout(() => URL.revokeObjectURL(url), 10_000)
  }

  const close = () => {
    if (opened.current) void api.closeScanEditor(opened.current)
    opened.current = null
    onClose()
  }

  const onPath = new Set(pieces.map((p) => p.scan))
  const unused = scans.filter((s) => !onPath.has(s.key)).map((s) => s.key)
  const warnings = path?.marks.filter((m) => m.warning) ?? []
  const top = path?.top ?? null
  const topEnergy = top !== null ? frames[top]?.energy : null
  const firstEnergy = frames[0]?.energy ?? null

  return (
    <Modal
      title="Scan editor"
      onClose={close}
      wide
      actions={
        <>
          <button onClick={back} disabled={busy || undo.length === 0} title="Undo the last change to the path (Ctrl+Z)">
            Undo
          </button>
          <button onClick={forward} disabled={busy || redo.length === 0} title="Redo (Ctrl+Y)">
            Redo
          </button>
          <button
            onClick={save}
            disabled={!path || path.frames.length < 2 || Boolean(path.blocked)}
            title="Save the joined path as an xyz file with its level, energies and where each structure came from"
          >
            Save as…
          </button>
          <button className="primary" onClick={close}>
            Close
          </button>
        </>
      }
    >
      <div className="scan-editor" aria-label="Scan editor">
        {error && <p role="alert">{error}</p>}
        {!editor && busy && <p className="muted">Reading the files…</p>}
        {editor && (
          <>
            {editor.problems.map((p) => (
              <p key={p.path} className="warning small">
                {p.problem}
              </p>
            ))}
            <div className="scan-editor-top">
              <section className="scan-list" aria-label="Scans">
                <div className="scan-list-head">
                  <h3>Scans</h3>
                  <button className="small" onClick={() => setAdding(!adding)} aria-expanded={adding}>
                    Add files…
                  </button>
                  {unused.length > 1 && (
                    <button
                      className="small"
                      disabled={busy}
                      onClick={() => change(pieces, { chain: unused })}
                      title="Join on every scan that starts or ends where the path does"
                    >
                      Join where they meet
                    </button>
                  )}
                </div>
                {adding && (
                  <div className="scan-add">
                    <FileBrowser
                      onPick={(p) => void addFiles([p])}
                      matching={{
                        label: (n) => `Add the ${n} matching files`,
                        title: 'Add every file the filter matches; those that meet the path are joined on',
                        onPick: (paths) => void addFiles(paths),
                      }}
                    />
                  </div>
                )}
                {scans.length === 0 && !adding && (
                  <p className="muted small">No scans yet: add the output files of one or more scans.</p>
                )}
                <ul aria-label="Scans in the editor">
                  {scans.map((scan) => (
                    <li key={scan.key} className={onPath.has(scan.key) ? 'on-path' : undefined}>
                      <span className="scan-swatch" style={{ background: colour(scan.key) }} />
                      <div className="scan-about">
                        <div title={scan.path}>
                          <strong>{scanName(scan)}</strong>
                          {scan.joined && <span className="muted"> · joined scan</span>}
                        </div>
                        <div className="small">
                          {range(scan)}
                          {scan.found_by === 'structures' && (
                            <span
                              className="muted"
                              title="The file does not name the scanned coordinate; this one changes by the most even steps"
                            >
                              {' '}
                              (found from the structures)
                            </span>
                          )}
                        </div>
                        <div className="small muted">
                          {scan.level}
                          {scan.server_name ? ` · on ${scan.server_name}` : ''}
                        </div>
                        {scan.left_out > 0 && (
                          <div className="small warning">
                            {scan.left_out === 1
                              ? '1 point did not converge and is left out'
                              : `${scan.left_out} points did not converge and are left out`}
                          </div>
                        )}
                        {!scan.ended && (
                          <div className="small warning">The job did not end normally (still running?)</div>
                        )}
                      </div>
                      <div className="scan-actions">
                        <button
                          className="small"
                          disabled={busy}
                          onClick={() => change(pieces, { add: scan.key })}
                          title="Put the whole scan on the path where it meets it, the right way round"
                        >
                          Add to path
                        </button>
                        <button
                          className="small"
                          aria-label={`Remove ${scanName(scan)}`}
                          title="Take this scan out of the editor (and off the path)"
                          onClick={() => void removeScan(scan.key)}
                        >
                          ✕
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
              <ScanChart
                scans={scans}
                path={path}
                colour={colour}
                at={preview ? null : at}
                preview={preview}
                zeroEnergy={zeroEnergy}
                factor={factor}
                unit={unit}
                onPoint={(p) => {
                  setPlaying(false)
                  setPreview(p)
                }}
                onFrame={show}
              />
            </div>

            <section className="scan-strip" aria-label="Path">
              <h3>Path</h3>
              {pieces.length === 0 && (
                <p className="muted small">Add a scan to start the path; the next one goes on where it meets it.</p>
              )}
              <ol>
                {pieces.map((piece, n) => {
                  const scan = byKey.get(piece.scan)
                  if (!scan) return null
                  const last = scan.points.length - 1
                  const inside = frame && frame.piece === n ? frame.index : null
                  const canSplit = inside !== null && inside !== piece.first && inside !== piece.last
                  const setEnd = (end: 'first' | 'last', typed: string) => {
                    const v = Number(typed) - 1
                    const other = end === 'first' ? piece.last : piece.first
                    if (Number.isInteger(v) && v >= 0 && v <= last && v !== other) {
                      change(replaced(pieces, n, [{ ...piece, [end]: v }]))
                    }
                  }
                  return (
                    <li
                      key={`${n}:${piece.scan}:${piece.first}:${piece.last}`}
                      style={{ borderColor: colour(piece.scan) }}
                      aria-label={`Piece ${n + 1}`}
                    >
                      <div className="piece-name" title={scan.path}>
                        {scanName(scan)}
                      </div>
                      <div className="piece-range small">
                        <label>
                          from{' '}
                          <input
                            type="number"
                            aria-label="First point"
                            min={1}
                            max={last + 1}
                            defaultValue={piece.first + 1}
                            onBlur={(event) => setEnd('first', event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') setEnd('first', event.currentTarget.value)
                            }}
                          />
                        </label>{' '}
                        {piece.last < piece.first ? '←' : '→'}{' '}
                        <label>
                          to{' '}
                          <input
                            type="number"
                            aria-label="Last point"
                            min={1}
                            max={last + 1}
                            defaultValue={piece.last + 1}
                            onBlur={(event) => setEnd('last', event.target.value)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') setEnd('last', event.currentTarget.value)
                            }}
                          />
                        </label>{' '}
                        <span className="muted">of {last + 1}</span>
                      </div>
                      <div className="piece-actions">
                        <button
                          className="small"
                          aria-label="Reverse"
                          title="Run this piece the other way"
                          disabled={busy}
                          onClick={() => change(replaced(pieces, n, [{ ...piece, first: piece.last, last: piece.first }]))}
                        >
                          ⇄
                        </button>
                        <button
                          className="small"
                          aria-label="Move earlier"
                          disabled={busy || n === 0}
                          onClick={() => change(swapped(pieces, n - 1, n))}
                        >
                          ◀
                        </button>
                        <button
                          className="small"
                          aria-label="Move later"
                          disabled={busy || n === pieces.length - 1}
                          onClick={() => change(swapped(pieces, n, n + 1))}
                        >
                          ▶
                        </button>
                        <button
                          className="small"
                          disabled={busy || !canSplit}
                          title={
                            canSplit
                              ? 'Split this piece at the frame the player shows'
                              : 'Step the player to a frame inside this piece to split it there'
                          }
                          onClick={() =>
                            inside !== null &&
                            change(replaced(pieces, n, [{ ...piece, last: inside }, { ...piece, first: inside }]))
                          }
                        >
                          Split here
                        </button>
                        <button
                          className="small"
                          aria-label="Remove from the path"
                          disabled={busy}
                          onClick={() => change(replaced(pieces, n, []))}
                        >
                          ✕
                        </button>
                      </div>
                    </li>
                  )
                })}
              </ol>
              {notes.map((note) => (
                <p key={note} className="muted small" role="status">
                  {note}
                </p>
              ))}
            </section>

            {path?.blocked && <p role="alert">{path.blocked}</p>}
            {path?.level_notes.map((note) => (
              <p key={note} className="muted small">
                {note}
              </p>
            ))}

            {(count > 0 || previewPoint) && (
              <div className="inspect-view">
                <Viewer3D
                  models={[
                    { xyz: previewPoint ? geometryToXyz(previewPoint.geometry, 'point') : xyzFrames[count - 1] },
                  ]}
                  trajectory={previewPoint || count === 0 ? null : { frames: xyzFrames, index: at }}
                />
                <div className="inspect-side step-movie">
                  {count > 1 && (
                    <div className="step-controls">
                      <button className="small" aria-label="Previous frame" onClick={() => move(-1)}>
                        ◀
                      </button>
                      <button
                        className="small"
                        aria-label={playing ? 'Pause' : 'Play'}
                        onClick={() => {
                          setPreview(null)
                          setPlaying(!playing)
                        }}
                      >
                        {playing ? '❚❚' : '▶'}
                      </button>
                      <button className="small" aria-label="Next frame" onClick={() => move(1)}>
                        ▶|
                      </button>
                      <input
                        type="range"
                        aria-label="Frame"
                        min={0}
                        max={count - 1}
                        value={at}
                        onChange={(event) => show(Number(event.target.value))}
                      />
                      <select aria-label="Speed" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
                        {SPEEDS.map((s) => (
                          <option key={s} value={s}>
                            {s}/s
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  {previewScan && previewPoint ? (
                    <p className="step-info small" aria-label="Structure shown">
                      {scanName(previewScan)}, point {previewPoint.number} of {previewScan.points.length} ·{' '}
                      {value(previewPoint.value, previewScan.unit)} {previewScan.unit} ·{' '}
                      <span className="mono">E = {hartree(previewPoint.energy)}</span>{' '}
                      <button className="link small" onClick={() => setPreview(null)}>
                        Back to the path
                      </button>
                    </p>
                  ) : (
                    frame &&
                    path && (
                      <p className="step-info small" aria-label="Frame shown">
                        Frame {at + 1} of {count} · {frame.source} ·{' '}
                        {path.axis === 'coordinate'
                          ? `${value(frame.x, path.unit)} ${path.unit}`
                          : `${frame.x.toFixed(2)} ${path.unit} along the path`}
                        {' · '}
                        <span className="mono">E = {hartree(frame.energy)}</span>
                        {frame.energy !== null && zeroEnergy !== null && (
                          <>
                            {' · '}
                            <span className="mono">
                              ΔE = {formatDelta(frame.energy - zeroEnergy, settings)} {unit}
                            </span>{' '}
                            <span className="muted">to frame {zero + 1}</span>
                          </>
                        )}
                      </p>
                    )
                  )}
                  <div className="inspect-copy">
                    <button className="small" onClick={() => void copyXyz()} title="Copy the structure shown as xyz text">
                      Copy XYZ
                    </button>
                    {!previewPoint && count > 0 && (
                      <button
                        className="small"
                        disabled={at === zero}
                        onClick={() => setZero(at)}
                        title="Measure ΔE from this frame (for instance the TS)"
                      >
                        Set as zero
                      </button>
                    )}
                    {copied && (
                      <span className="muted small" role="status">
                        {copied}
                      </span>
                    )}
                  </div>
                  {top !== null && topEnergy != null && firstEnergy !== null && (
                    <p className="small" aria-label="Top of the path">
                      Top at frame {top + 1}:{' '}
                      <span className="mono">
                        {formatDelta(topEnergy - firstEnergy, settings)} {unit}
                      </span>{' '}
                      above the first frame{' '}
                      <button className="link small" onClick={() => show(top)}>
                        Show
                      </button>
                    </p>
                  )}
                  {count > 1 && (
                    <EnergyChart
                      items={frames.map((f, n) => ({ n, energy: f.energy, removed: false, spike: false }))}
                      cuts={[]}
                      at={at}
                      last={zeroEnergy}
                      unit={unit}
                      settings={settings}
                      selected={null}
                      onPick={show}
                    />
                  )}
                  {path && path.marks.length > 0 && (
                    <ul className="scan-marks small" aria-label="Joins and checks">
                      {path.marks.map((m, k) => (
                        <li key={k} className={m.warning ? 'warning' : undefined}>
                          <button className="link" onClick={() => show(Math.min(m.at, count - 1))}>
                            {m.warning ? '⚠' : '✓'} frame {m.at + 1}
                          </button>{' '}
                          {m.text}
                        </li>
                      ))}
                    </ul>
                  )}
                  {path && count > 1 && path.joins.length > 0 && warnings.length === 0 && (
                    <p className="muted small">Every join is continuous.</p>
                  )}
                </div>
              </div>
            )}
            <p className="muted small">
              Read without importing: nothing is written to the investigation or to the files until Save
              {path?.level ? ` · ${path.level}` : ''}.
            </p>
          </>
        )}
      </div>
    </Modal>
  )
}

/** Every scan on one chart: x is the scanned coordinate (the path's, or the first scan's), y is
 * ΔE to the path's zero (another level against its own lowest point, dashed). A click shows the
 * nearest point's structure, or the path's frame; the path is drawn bold on top. */
function ScanChart({
  scans,
  path,
  colour,
  at,
  preview,
  zeroEnergy,
  factor,
  unit,
  onPoint,
  onFrame,
}: {
  scans: EditorScan[]
  path: JoinedPath | null
  colour: (key: string) => string
  at: number | null
  preview: Preview | null
  zeroEnergy: number | null
  factor: number
  unit: string
  onPoint: (point: Preview) => void
  onFrame: (index: number) => void
}) {
  const svg = useRef<SVGSVGElement>(null)
  const axisAtoms = path && path.axis === 'coordinate' && path.atoms.length ? path.atoms : (scans[0]?.atoms ?? [])
  const drawn = scans.filter((s) => sameAtoms(s.atoms, axisAtoms))
  const other = scans.length - drawn.length
  const level = path?.level || drawn[0]?.level
  // Each level's zero: the path's zero for its level, else its lowest point.
  const zeros = new Map<string, number>()
  for (const scan of drawn) {
    for (const p of scan.points) {
      if (p.energy === null) continue
      const known = zeros.get(scan.level)
      if (known === undefined || p.energy < known) zeros.set(scan.level, p.energy)
    }
  }
  if (level && zeroEnergy !== null) zeros.set(level, zeroEnergy)
  const showPath =
    path !== null && path.axis === 'coordinate' && sameAtoms(path.atoms, axisAtoms) && path.frames.length > 1
  type Dot = { i: number; x: number; y: number }
  const dots = (items: { x: number; energy: number | null }[], zeroAt: number | undefined): Dot[] =>
    items.flatMap((p, i) => (p.energy === null || zeroAt === undefined ? [] : [{ i, x: p.x, y: (p.energy - zeroAt) * factor }]))
  const lines = drawn.map((scan) => ({
    scan,
    points: dots(
      scan.points.map((p) => ({ x: p.value, energy: p.energy })),
      zeros.get(scan.level),
    ),
  }))
  const pathPoints = showPath && level ? dots(path.frames, zeros.get(level)) : []
  const all = [...lines.flatMap((l) => l.points), ...pathPoints]
  if (all.length < 2) {
    return <div className="scan-chart muted small">The chart shows the scans once they have points with energies.</div>
  }
  const width = 520
  const height = 220
  const left = 46
  const right = 8
  const top = 8
  const bottom = 28
  const xs = all.map((p) => p.x)
  const ys = all.map((p) => p.y)
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)]
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)]
  const sx = (v: number) => left + ((v - x0) / (x1 - x0 || 1)) * (width - left - right)
  const sy = (v: number) => top + ((y1 - v) / (y1 - y0 || 1)) * (height - top - bottom)
  const scanUnit = drawn[0]?.unit ?? ''
  const nearest = (event: PointerEvent<SVGSVGElement>) => {
    const box = svg.current?.getBoundingClientRect()
    if (!box) return
    const px = ((event.clientX - box.left) / box.width) * width
    const py = ((event.clientY - box.top) / box.height) * height
    let best: { d: number; scan?: string; i: number } | null = null
    for (const p of pathPoints) {
      const d = Math.hypot(sx(p.x) - px, sy(p.y) - py)
      if (!best || d < best.d) best = { d, i: p.i }
    }
    for (const line of lines) {
      for (const p of line.points) {
        const d = Math.hypot(sx(p.x) - px, sy(p.y) - py) + 0.5 // the path wins a tie
        if (!best || d < best.d) best = { d, scan: line.scan.key, i: p.i }
      }
    }
    if (!best || best.d > 14) return
    if (best.scan) onPoint({ scan: best.scan, index: best.i })
    else onFrame(best.i)
  }
  const line = (points: Dot[]) => points.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')
  const shownFrame = at !== null ? pathPoints.find((p) => p.i === at) : undefined
  const shownPoint = preview
    ? lines.find((l) => l.scan.key === preview.scan)?.points.find((p) => p.i === preview.index)
    : undefined
  const middle = (top + height - bottom) / 2
  return (
    <div className="scan-chart">
      <svg
        ref={svg}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="Scans by coordinate"
        onPointerUp={nearest}
      >
        <line x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} stroke="var(--border)" />
        <line x1={left} x2={left} y1={top} y2={height - bottom} stroke="var(--border)" />
        <text x={left} y={height - 8} fontSize={10} fill="currentColor">
          {value(x0, scanUnit)}
        </text>
        <text x={width - right} y={height - 8} fontSize={10} textAnchor="end" fill="currentColor">
          {value(x1, scanUnit)} {scanUnit}
        </text>
        <text x={(left + width) / 2} y={height - 8} fontSize={10} textAnchor="middle" fill="currentColor">
          {drawn[0]?.coordinate}
        </text>
        <text x={left - 4} y={top + 8} fontSize={10} textAnchor="end" fill="currentColor">
          {y1.toFixed(1)}
        </text>
        <text x={left - 4} y={height - bottom} fontSize={10} textAnchor="end" fill="currentColor">
          {y0.toFixed(1)}
        </text>
        <text x={10} y={middle} fontSize={10} textAnchor="middle" fill="currentColor" transform={`rotate(-90 10 ${middle})`}>
          ΔE ({unit})
        </text>
        {lines.map(({ scan, points }) => (
          <g key={scan.key} stroke={colour(scan.key)} fill={colour(scan.key)}>
            <polyline
              points={line(points)}
              fill="none"
              strokeWidth={1.2}
              strokeDasharray={level && scan.level !== level ? '4 3' : undefined}
              opacity={0.75}
            />
            {points.map((p) => (
              <circle key={p.i} cx={sx(p.x)} cy={sy(p.y)} r={2.2} stroke="none" />
            ))}
          </g>
        ))}
        {pathPoints.length > 1 && (
          <polyline points={line(pathPoints)} fill="none" stroke="var(--text)" strokeWidth={2.4} />
        )}
        {shownFrame && (
          <circle cx={sx(shownFrame.x)} cy={sy(shownFrame.y)} r={5} fill="none" stroke="var(--text)" strokeWidth={2} />
        )}
        {shownPoint && (
          <circle
            cx={sx(shownPoint.x)}
            cy={sy(shownPoint.y)}
            r={5}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={2}
          />
        )}
      </svg>
      <p className="muted small">
        Click a point to see its structure; the path is the bold line.
        {drawn.some((s) => level && s.level !== level) && ' Dashed: another level of theory, against its own lowest point.'}
        {other === 1 && ' 1 scan drives another coordinate and is not drawn.'}
        {other > 1 && ` ${other} scans drive another coordinate and are not drawn.`}
      </p>
    </div>
  )
}
