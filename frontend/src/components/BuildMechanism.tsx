import { useEffect, useState } from 'react'
import { api, type ClaudeAutonomy, type ClaudeStatus } from '../api'
import { buildMechanismCommand, errorText, limitText } from '../util'
import { AutonomyDialog, type BuildRun } from './ClaudePanel'
import { FolderPicker } from './FolderPicker'
import { Modal } from './Modal'

/** D263f: the longest local job Claude may start for a build, changeable in the start dialog. */
export const BUILD_JOB_LIMIT = 4 * 3600
/** D263f, D265d, A185: the same while filling gaps with MACE guesses (TS guesses take longer). */
export const GAPS_JOB_LIMIT = 6 * 3600

/** D263a, FR-PANEL-09: the start dialog of "Build mechanism from folder…": the folder of
 * outputs, the build's job limit, the literature tick box (D263h) and the gaps tick box (D265d;
 * ticked, the limit defaults to 6 h, else 4 h, until the person picks one). Start shows the Work on
 * its own warning (D223), unless the switch is on here already; accepting it hands the build
 * to `onStart`, which makes a playground of an investigation first (Claude never makes one). */
export function BuildMechanismDialog({
  playground,
  onCancel,
  onStart,
}: {
  /** The open investigation is a playground already. */
  playground: boolean
  onCancel: () => void
  /** For an investigation, resolves once the playground is open (a refusal shows here). */
  onStart: (build: BuildRun) => Promise<void> | void
}) {
  const [status, setStatus] = useState<ClaudeStatus | null>(null)
  const [autonomy, setAutonomy] = useState<ClaudeAutonomy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [folder, setFolder] = useState('')
  const [lastFolder, setLastFolder] = useState<string | undefined>(undefined)
  const [chosenLimit, setChosenLimit] = useState<number | null>(null)
  const [literature, setLiterature] = useState(true)
  const [gaps, setGaps] = useState(true)
  const limit = chosenLimit ?? (gaps ? GAPS_JOB_LIMIT : BUILD_JOB_LIMIT)
  const [stage, setStage] = useState<'form' | 'browse' | 'warning'>('form')

  useEffect(() => {
    api.claudeStatus().then(setStatus, (err: unknown) => setError(errorText(err)))
    api.claudeAutonomy().then(setAutonomy, (err: unknown) => setError(errorText(err)))
    api.settings().then(
      (settings) => setLastFolder(settings.last_import_folder || undefined),
      () => undefined,
    )
  }, [])

  const usable = status !== null && status.enabled && status.available && status.notebook_tools
  const chosen = folder.trim()
  const text = buildMechanismCommand(chosen, literature, gaps)
  const limits = [...new Set([...(autonomy?.job_limits ?? []), BUILD_JOB_LIMIT, GAPS_JOB_LIMIT])].sort((a, b) => a - b)

  if (stage === 'browse')
    return (
      <FolderPicker
        mode="folder"
        title="Folder of outputs"
        initialPath={chosen || lastFolder}
        onCancel={() => setStage('form')}
        onChoose={(path) => {
          setFolder(path)
          setStage('form')
        }}
      />
    )

  if (stage === 'warning' && autonomy)
    return (
      <AutonomyDialog
        autonomy={autonomy}
        playground={playground}
        build={{ jobLimit: limit }}
        onCancel={() => setStage('form')}
        onTurnOn={(jobLimit, cloud) => void onStart({ text, jobLimit, cloud })}
        onPlaygroundFirst={async (jobLimit, cloud) => {
          await onStart({ text, jobLimit, cloud })
        }}
      />
    )

  const start = () => {
    if (!chosen || !usable || autonomy === null) return
    // On already in this playground: its warning was accepted; the build's limit applies.
    if (playground && autonomy.on) void onStart({ text, jobLimit: limit, cloud: autonomy.cloud })
    else setStage('warning')
  }

  return (
    <Modal
      title="Build mechanism from folder"
      onClose={onCancel}
      actions={
        <>
          <button onClick={onCancel}>Cancel</button>
          <button className="primary" disabled={!chosen || !usable || autonomy === null} onClick={start}>
            Start…
          </button>
        </>
      }
    >
      <p>
        Claude reads every output in the folder, imports it into {playground ? 'this playground' : 'a playground of this investigation'}{' '}
        and links the structures into a mechanism, working on its own. Each edge it proposes carries its evidence and
        waits for your review; what it cannot settle it flags in a summary note.
      </p>
      <div className="path-row">
        <input
          aria-label="Folder of outputs"
          placeholder="The folder with the outputs"
          value={folder}
          onChange={(event) => setFolder(event.target.value)}
        />
        <button onClick={() => setStage('browse')}>Browse…</button>
      </div>
      <label>
        Longest local job Claude may start{' '}
        <select value={limit} onChange={(event) => setChosenLimit(Number(event.target.value))}>
          {limits.map((seconds) => (
            <option key={seconds} value={seconds}>
              {limitText(seconds)}
            </option>
          ))}
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={literature} onChange={(event) => setLiterature(event.target.checked)} /> Use
        the literature where it helps
      </label>
      <label className="check">
        <input type="checkbox" checked={gaps} onChange={(event) => setGaps(event.target.checked)} /> Fill gaps with
        MACE guesses
      </label>
      {gaps && (
        <p className="muted small">
          Up to 10 missing minima, transition states or free species are built with MACE-OMOL-0 between the structures
          the folder has, as planned nodes marked “MACE guess” with low-confidence edges.
        </p>
      )}
      {chosen && (
        <p className="muted small">
          Claude is started in its panel with <code>{text}</code>, where you can follow it and stop it.
        </p>
      )}
      {status !== null && !usable && (
        <p role="alert">
          {status.reason ??
            (status.available
              ? 'This version of Chembook3D has no notebook tools for Claude.'
              : 'Claude Code is not installed on this computer; the Claude panel says how to install it.')}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </Modal>
  )
}
