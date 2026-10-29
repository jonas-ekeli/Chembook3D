import { useMemo } from 'react'
import { energyTypeName, type EnergyType, type Settings } from '../api'
import { EnergyNotes } from '../components/EnergyNotes'
import { EnergyTableView } from '../components/EnergyTableView'
import { GModelBadge } from '../components/GModelBadge'
import { Menu } from '../components/MoreMenu'
import { ProfileChart, SCREEN_TEXT } from '../components/ProfileChart'
import { Icon } from '../icons'
import { figureOf } from '../profileFigure'
import { pathwayColours, saveProfilePng, saveProfileSvg, styleOf } from '../profileStyle'
import { closesCycle, csvTitle, download } from '../util'
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
  tab,
  onTab,
  onSelectNode,
  selectedId,
}: {
  data: SnapshotData
  settings: Settings
  level: string | null
  levelLabel: string
  type: EnergyType
  referenceId: string | null
  onReference: (id: string) => void
  tab: 'profile' | 'table'
  onTab: (tab: 'profile' | 'table') => void
  onSelectNode: (id: string) => void
  /** D177: the node or group selected on the canvas, whose table row is highlighted. */
  selectedId: string | null
}) {
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
  const shifts = paths.map((p) => p.shifts ?? []) // D194
  const chosen = paths.map((p) => p.colour) // D195
  const drawnColours = pathwayColours(colours, styleOf(settings), chosen)

  // D181: the saved figure is drawn afresh in the style alone, as the app saves it.
  const figure = () => shown && figureOf({ data: shown.profiles, colours, names, settings, shifts, chosen })

  const exportSvg = () => {
    const svg = figure()
    if (svg) saveProfileSvg(svg)
  }

  const exportPng = () => {
    const svg = figure()
    if (svg) saveProfilePng(svg, styleOf(settings), () => undefined)
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
          <button aria-pressed={tab === 'profile'} onClick={() => onTab('profile')} title="Energy profile">
            Profile
          </button>
          <button aria-pressed={tab === 'table'} onClick={() => onTab('table')} title="Energy table">
            Table
          </button>
        </div>
        <span className="muted small">
          {level
            ? `${energyTypeName(type, settings.qh_temperature, settings.qh_cutoff, settings.standard_state, shown?.profiles.job_temperatures)} at ${levelLabel}`
            : 'No energies in this investigation'}
        </span>
        {type === 'G' && <GModelBadge models={shown?.profiles.g_models} />}
        <span className="spacer" />
        <Menu
          label="Export"
          buttonClass="small"
          buttonLabel="Export ▾"
          title="The profile as PNG or SVG, the table as CSV"
          items={[
            { label: 'Profile as PNG', onSelect: exportPng, disabled: !shown },
            { label: 'Profile as SVG', onSelect: exportSvg, disabled: !shown },
            { label: 'Table as CSV', onSelect: exportCsv, disabled: !shown, title: csvTitle(type) },
          ]}
        >
          <Icon name="download" />
          Export
          <Icon name="chevron" />
        </Menu>
      </div>
      <div className="drawer-body">
        <div className="pathways" aria-label="Pathways">
          {paths.map((path, index) => (
            <div key={index} className="pathway" role="group" aria-label={`Pathway ${names[index]}`}>
              <div className="pathway-head">
                <span className="swatch" style={{ background: drawnColours[index] }} />
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
            <>
              <EnergyNotes notes={shown.profiles.notes} />
              <ProfileChart
                data={shown.profiles}
                colours={colours}
                names={names}
                shifts={shifts}
                chosen={chosen}
                settings={settings}
                textScale={SCREEN_TEXT}
                pointing={{
                  selectedId,
                  onSelect: onSelectNode,
                  branchName: (id) => {
                    const branch = branches.get(id)
                    return branch && (branch.name || 'Unnamed branch')
                  },
                }}
              />
            </>
          ) : (
            <EnergyTableView
              table={shown.table}
              type={type}
              reference={reference ? (labels.get(reference) ?? null) : null}
              colourOf={(id) => branches.get(id)?.colour}
              selectedId={selectedId}
            />
          )}
        </div>
      </div>
    </section>
  )
}
