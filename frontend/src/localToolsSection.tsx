import { useCallback, useEffect, useState } from 'react'
import { api, type LocalTools } from './api'
import { InstallControl } from './components/LocalTools'
import { NumberSetting } from './components/NumberSetting'
import type { SettingSection } from './components/SettingsDialog'
import { SETTINGS_SECTIONS } from './settingsSections'
import { errorText } from './util'

/** How often the section looks again while an install runs. */
const INSTALL_POLL = 2000

/** D213: the tools' status, shared by the section's rows while the dialog is open. */
function useLocalTools(): [LocalTools | null, (next: LocalTools) => void, string | null] {
  const [tools, setTools] = useState<LocalTools | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(
    () =>
      api.localTools().then(
        (found) => {
          setTools(found)
          setError(null)
        },
        (err: unknown) => setError(errorText(err)),
      ),
    [],
  )
  useEffect(() => {
    void load()
  }, [load])
  const running = tools?.install.state === 'running'
  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => void load(), INSTALL_POLL)
    return () => window.clearInterval(timer)
  }, [running, load])
  return [tools, setTools, error]
}

/** D213, FR-LOC-01: the Settings section "Local calculations": installing the tools (Psi4,
 * tblite and geomeTRIC from conda-forge into the app's own folder) and the cores and memory a
 * job may use on this computer. */
export function useLocalToolsSection(): SettingSection {
  const [tools, setTools, error] = useLocalTools()
  const saveResources = (fields: { cores?: number | null; memory_mb?: number | null }) =>
    api
      .saveLocalResources({
        cores: fields.cores !== undefined ? fields.cores : (tools?.cores ?? null),
        memory_mb: fields.memory_mb !== undefined ? fields.memory_mb : (tools?.memory_mb ?? null),
      })
      .then(setTools)
  return {
    id: 'local',
    title: SETTINGS_SECTIONS.local,
    icon: 'flask',
    scope: 'computer',
    badge: 'This computer',
    rows: [
      {
        name: 'Tools for local calculations',
        help: (
          <>
            Refine geometry runs GFN2-xTB on this computer with tblite and geomeTRIC; Psi4 comes along for the DFT
            methods. One download of about 1.5 GB from conda-forge, no account.
            {error && <span className="cloud-job-problem"> {error}</span>}
          </>
        ),
        more: (
          <>
            They go into {tools?.folder ?? 'the app’s data folder'}, not into an investigation; the app keeps running until
            the install is done (5–10 min), even if the tab is closed. Each job runs as a program of its own, so it goes on when
            the app is closed, and jobs run one after another.
          </>
        ),
        control: <InstallControl tools={tools} onTools={setTools} />,
      },
      {
        name: 'Cores',
        help: tools
          ? `For each job. The default, ${tools.default_cores}, leaves one core of this computer free (it has ${tools.logical_cores} threads).`
          : 'For each job.',
        control: tools ? (
          <NumberSetting
            key={`cores:${tools.cores}`}
            label="Cores for local jobs"
            value={tools.cores}
            onSave={(cores) => void saveResources({ cores: Math.max(1, Math.round(cores)) })}
          />
        ) : (
          <span className="muted">…</span>
        ),
      },
      {
        name: 'Memory',
        help: tools?.total_memory_mb
          ? `For each job; this computer has ${Math.round(tools.total_memory_mb / 1000)} GB.`
          : 'For each job.',
        control: tools ? (
          <NumberSetting
            key={`memory:${tools.memory_mb}`}
            label="Memory for local jobs (GB)"
            unit="GB"
            value={Math.round(tools.memory_mb / 100) / 10}
            onSave={(gb) => void saveResources({ memory_mb: Math.max(500, Math.round(gb * 1000)) })}
          />
        ) : (
          <span className="muted">…</span>
        ),
      },
    ],
  }
}
