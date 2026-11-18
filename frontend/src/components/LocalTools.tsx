import { useState } from 'react'
import { api, type LocalTools } from '../api'
import { errorText } from '../util'

function minutes(seconds: number | null): string {
  if (seconds === null) return ''
  return seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`
}

/** D216: the optional g-xTB install, once the tools are there. */
export function GxtbControl({ tools, onTools }: { tools: LocalTools | null; onTools: (t: LocalTools) => void }) {
  const [error, setError] = useState<string | null>(null)
  if (!tools) return <span className="muted">…</span>
  if (!tools.gxtb.supported) return <span className="muted">Not offered on this computer</span>
  const running = tools.gxtb.install.state === 'running'
  const label = !tools.gxtb.installed ? 'Install g-xTB' : tools.gxtb.up_to_date ? 'Reinstall g-xTB' : 'Update g-xTB'
  return (
    <div className="local-tools-install">
      <button
        disabled={running || !tools.installed}
        onClick={() => {
          setError(null)
          api.installGxtb().then(onTools, (err: unknown) => setError(errorText(err)))
        }}
      >
        {running ? 'Installing…' : label}
      </button>
      <div className="small" role="status" aria-label="g-xTB status">
        {running && tools.gxtb.install.step}
        {!running && tools.gxtb.install.state === 'failed' && (
          <span className="cloud-job-problem">Install failed: {tools.gxtb.install.error}</span>
        )}
        {!running && tools.gxtb.install.state !== 'failed' &&
          (tools.gxtb.installed
            ? `Installed · ${tools.gxtb.version}${tools.gxtb.up_to_date ? '' : '; this version of the app has a newer one'}`
            : tools.installed
              ? 'Not installed'
              : 'Install the tools first')}
      </div>
      {error && (
        <div className="small cloud-job-problem" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}

/** D237: the optional MACE-OMOL-0 install: its licence is accepted with a tick before the
 * download is offered. */
export function MaceControl({ tools, onTools }: { tools: LocalTools | null; onTools: (t: LocalTools) => void }) {
  const [error, setError] = useState<string | null>(null)
  const [accepted, setAccepted] = useState(false)
  if (!tools) return <span className="muted">…</span>
  const mace = tools.mace
  if (!mace.supported) return <span className="muted">Not offered on this computer</span>
  const running = mace.install.state === 'running'
  const label = !mace.installed ? 'Install MACE-OMOL-0' : mace.up_to_date ? 'Reinstall MACE' : 'Update MACE'
  return (
    <div className="local-tools-install">
      {!mace.installed && (
        <label className="small">
          <input type="checkbox" checked={accepted} disabled={running} onChange={(e) => setAccepted(e.target.checked)} /> I
          accept the model’s{' '}
          <a href={mace.licence.url} target="_blank" rel="noreferrer">
            {mace.licence.name}
          </a>{' '}
          ({mace.licence.summary})
        </label>
      )}
      <button
        disabled={running || (!mace.installed && !accepted)}
        onClick={() => {
          setError(null)
          api.installMace(mace.installed || accepted).then(onTools, (err: unknown) => setError(errorText(err)))
        }}
      >
        {running ? 'Installing…' : label}
      </button>
      <div className="small" role="status" aria-label="MACE status">
        {running && `${mace.install.step} · ${minutes(mace.install.seconds)}`}
        {!running && mace.install.state === 'failed' && (
          <span className="cloud-job-problem">Install failed: {mace.install.error}</span>
        )}
        {!running &&
          mace.install.state !== 'failed' &&
          (mace.installed
            ? `Installed${mace.up_to_date ? '' : '; this version of the app has a newer one'}${
                mace.versions['mace-torch'] ? ` · mace-torch ${mace.versions['mace-torch']}` : ''
              }${mace.versions.torch ? ` · PyTorch ${mace.versions.torch}` : ''}`
            : 'Not installed')}
      </div>
      {error && (
        <div className="small cloud-job-problem" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}

export function InstallControl({ tools, onTools }: { tools: LocalTools | null; onTools: (t: LocalTools) => void }) {
  const [error, setError] = useState<string | null>(null)
  if (!tools) return <span className="muted">…</span>
  if (!tools.supported) return <span className="muted">Not offered on this computer</span>
  const running = tools.install.state === 'running'
  const label = !tools.installed ? 'Install' : tools.up_to_date ? 'Reinstall' : 'Update tools'
  return (
    <div className="local-tools-install">
      <button
        className={tools.installed && tools.up_to_date ? undefined : 'primary'}
        disabled={running}
        onClick={() => {
          setError(null)
          api.installLocalTools().then(onTools, (err: unknown) => setError(errorText(err)))
        }}
      >
        {running ? 'Installing…' : label}
      </button>
      <div className="small" role="status" aria-label="Local tools status">
        {running && `${tools.install.step} · ${minutes(tools.install.seconds)}`}
        {!running && tools.install.state === 'failed' && (
          <span className="cloud-job-problem">Install failed: {tools.install.error}</span>
        )}
        {!running && tools.install.state !== 'failed' && tools.installed && (
          <>
            {tools.up_to_date ? 'Installed' : 'Installed; this version of the app has newer tools'}
            {tools.versions.psi4 && ` · Psi4 ${tools.versions.psi4}`}
            {tools.versions.tblite && ` · tblite ${tools.versions.tblite}`}
            {tools.versions.geometric && ` · geomeTRIC ${tools.versions.geometric}`}
          </>
        )}
        {!running && tools.install.state !== 'failed' && !tools.installed && 'Not installed'}
      </div>
      {error && (
        <div className="small cloud-job-problem" role="alert">
          {error}
        </div>
      )}
    </div>
  )
}

