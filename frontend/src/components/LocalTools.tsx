import { useState } from 'react'
import { api, type LocalTools } from '../api'
import { errorText } from '../util'

function minutes(seconds: number | null): string {
  if (seconds === null) return ''
  return seconds < 90 ? `${seconds} s` : `${Math.round(seconds / 60)} min`
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

