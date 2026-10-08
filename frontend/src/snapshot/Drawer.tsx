import { useMemo, useRef, useState } from 'react'
import { energyTypeName, type EnergyType, type Settings } from '../api'
import { ProfileChart } from '../components/ProfileChart'
import { saveProfilePng, saveProfileSvg, styleOf } from '../profileStyle'
import { closesCycle, download } from '../util'
import { tableCsv, type SnapshotData } from './data'

const NO_BRANCH = '#98a2b3'
const FALLBACK = ['#2459c6', '#c4320a', '#079455', '#6938ef', '#b54708']

/** The profile and table of the exported pathways (A30), for the chosen level, type and
 * reference; the reader cannot add pathways, since the app computed each one when exporting. */
export function SnapshotDrawer({
  data,
  settings,
  level,
  levelLabel,
  type,
  referenceId,
  onReference,
  onSelectNode,
}: {
  data: SnapshotData
  settings: Settings
  level: string | null
  levelLabel: string
  type: EnergyType
  referenceId: string | null
  onReference: (id: string) => void
  onSelectNode: (id: string) => void
}) {
  const [tab, setTab] = useState<'profile' | 'table'>('profile')
  const svgRef = useRef<SVGSVGElement>(null)
  const canvas = data.canvas
  const paths = data.paths

  const labels = useMemo(() => {
    const found = new Map<string, string>()
    for (const n of canvas.nodes) found.set(n.id, n.label || 'Untitled node')
    for (const g of canvas.groups) found.set(g.id, g.label || 'Group')
    return found
  }, [canvas])
  const branches = new Map(canvas.branches.map((b) => [b.id, b]))
  const branchOf = (id: string) =>
    canvas.nodes.find((n) => n.id === id)?.branch_id ?? canvas.groups.find((g) => g.id === id)?.outgoing_branch_id ?? null
  const onPaths = [...new Set(paths.flatMap((p) => p.ids))]
  const reference = referenceId && onPaths.includes(referenceId) ? referenceId : (paths[0]?.ids[0] ?? null)
  const colours = paths.map((p, i) => {
    const branchId = p.branch_id ?? [...p.ids].reverse().map(branchOf).find((b) => b)
    return branchId ? (branches.get(branchId)?.colour ?? NO_BRANCH) : FALLBACK[i % FALLBACK.length]
  })
  const names = paths.map((p) => {
    const branch = p.branch_id ? branches.get(p.branch_id) : undefined
    return branch ? branch.name || 'Unnamed branch' : `${labels.get(p.ids[0])} → ${labels.get(p.ids.at(-1)!)}`
  })
  const shown = level && reference ? data.profiles[`${level}|${type}|${reference}`] : undefined

  const exportSvg = () => {
    if (svgRef.current) saveProfileSvg(svgRef.current)
  }

  const exportPng = () => {
    if (svgRef.current) saveProfilePng(svgRef.current, styleOf(settings), () => undefined)
  }

  const exportCsv = () => {
    if (!shown) return
    const url = URL.createObjectURL(new Blob([tableCsv(shown.table)], { type: 'text/csv;charset=utf-8' }))
    download(url, 'energy-table.csv')
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <section className="drawer" aria-label="Energy drawer">
      <div className="drawer-head">
        <div className="segmented" role="group" aria-label="Drawer tab">
          <button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>
            Energy profile
          </button>
          <button aria-pressed={tab === 'table'} onClick={() => setTab('table')}>
            Energy table
          </button>
        </div>
        <span className="muted small">
          {level
            ? `${energyTypeName(type, settings.qh_temperature, settings.qh_cutoff, settings.standard_state)} at ${levelLabel}`
            : 'No energies in this investigation'}
        </span>
        <span className="spacer" />
        {tab === 'profile' ? (
          <>
            <button className="small" onClick={exportPng} disabled={!shown}>
              Save profile as PNG
            </button>
            <button className="small" onClick={exportSvg} disabled={!shown}>
              Save profile as SVG
            </button>
          </>
        ) : (
          <button className="small" onClick={exportCsv} disabled={!shown}>
            Export CSV
          </button>
        )}
      </div>
      <div className="drawer-body">
        <div className="pathways" aria-label="Pathways">
          {paths.map((path, index) => (
            <div key={index} className="pathway" role="group" aria-label={`Pathway ${names[index]}`}>
              <div className="pathway-head">
                <span className="swatch" style={{ background: colours[index] }} />
                <strong>{names[index]}</strong>
              </div>
              <p className="pathway-nodes">
                {path.ids.map((id, i) => (
                  <span key={i}>
                    {i > 0 && ' → '}
                    <button className="link" onClick={() => onSelectNode(id)}>
                      {labels.get(id)}
                    </button>
                    {i === path.ids.length - 1 && closesCycle(path.ids) && (
                      <span className="cycle-mark" title="Cycle closed">
                        {' '}
                        ↻
                      </span>
                    )}
                  </span>
                ))}
              </p>
            </div>
          ))}
          {paths.length > 0 && (
            <label className="field">
              <span>Reference</span>
              <select
                aria-label="Reference node"
                value={reference ?? ''}
                onChange={(event) => onReference(event.target.value)}
              >
                {onPaths.map((id) => (
                  <option key={id} value={id}>
                    {labels.get(id)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        <div className="drawer-main">
          {!paths.length ? (
            <p className="muted placeholder">No pathways were included in this copy.</p>
          ) : !shown ? (
            <p className="muted placeholder">No profile at this level and energy type.</p>
          ) : tab === 'profile' ? (
            <ProfileChart data={shown.profiles} colours={colours} names={names} settings={settings} svgRef={svgRef} />
          ) : (
            <table className="energy-table" aria-label="Energy table">
              <thead>
                <tr>
                  {shown.table.columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.table.rows.map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j} className={j >= 4 ? 'num' : undefined}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  )
}
