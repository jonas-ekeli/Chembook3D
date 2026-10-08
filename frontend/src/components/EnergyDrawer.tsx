import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  energyTypeName,
  type Canvas,
  type EnergyTable,
  type EnergyType,
  type Node,
  type Profiles,
  type ProfileRequest,
  type Settings,
  type ViewState,
} from '../api'
import { saveProfilePng, saveProfileSvg, styleOf } from '../profileStyle'
import { closesCycle, download } from '../util'
import { ProfileChart } from './ProfileChart'
import { ProfileStyleDialog } from './ProfileStyleDialog'
import { CompareStericsDialog } from './Sterics'

const NO_BRANCH = '#98a2b3'
const FALLBACK = ['#2459c6', '#c4320a', '#079455', '#6938ef', '#b54708']

type Path = { ids: string[]; choices: { node_id: string; label: string; status: string }[]; branchId: string | null }

/** A pathway as the read-only copy exports it (D79, A30). */
export type DrawerPath = { ids: string[]; branch_id: string | null }

/** D105: the drawer as the investigation remembers it. */
export type DrawerView = Omit<ViewState['drawer'], 'open'>

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

// D106: the drawer's height and whether its pathways list shows, remembered by the browser.
const LAYOUT = 'chembook3d.drawer'
type Layout = { height: number; pathways: boolean }
const DEFAULT_LAYOUT: Layout = { height: 380, pathways: true }

function loadLayout(): Layout {
  try {
    const stored = JSON.parse(localStorage.getItem(LAYOUT) ?? 'null') as Partial<Layout> | null
    return {
      height: typeof stored?.height === 'number' ? stored.height : DEFAULT_LAYOUT.height,
      pathways: stored?.pathways !== false,
    }
  } catch {
    return DEFAULT_LAYOUT
  }
}

/** Between a strip that still shows the drawer's head and most of the window. */
function drawerHeight(height: number): number {
  return Math.round(Math.min(Math.max(180, window.innerHeight - 140), Math.max(180, height)))
}

// Zoom steps of the profile, as a share of the figure's own size (D106).
const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4]
const clampZoom = (zoom: number) => Math.min(4, Math.max(0.5, zoom))

/** WF-08 bottom drawer: pathways chosen along edges, the energy profile and the energy table. */
export function EnergyDrawer({
  canvas,
  settings,
  level,
  levelLabel,
  type,
  referenceId,
  selectedId,
  refreshKey,
  onReference,
  onSelectNode,
  onSettings,
  onPathsChange,
  onTurnover,
  saved,
  onViewChange,
}: {
  canvas: Canvas
  settings: Settings | null
  level: string | null
  levelLabel: string
  type: EnergyType
  referenceId: string | null
  selectedId: string | null
  refreshKey: number
  onReference: (id: string | null) => void
  onSelectNode: (id: string) => void
  /** D106: the app's settings after the profile style was saved. */
  onSettings?: (settings: Settings) => void
  /** The pathways shown, for the read-only copy (D79). */
  onPathsChange?: (paths: DrawerPath[]) => void
  /** D86: a saved turnover from a closed pathway, opened in the Analyses view. */
  onTurnover?: (ids: string[], name: string) => void
  /** D105: the tab and pathways to start with, as the investigation remembered them. */
  saved?: DrawerView
  /** D105: the tab and pathways, whenever they change, to be remembered. */
  onViewChange?: (view: DrawerView) => void
}) {
  const [tab, setTab] = useState<'profile' | 'table'>(saved?.tab ?? 'profile')
  const [paths, setPaths] = useState<Path[]>(
    () => saved?.paths.map((p) => ({ ids: p.ids, choices: p.choices, branchId: p.branch_id })) ?? [],
  )
  const [profiles, setProfiles] = useState<Profiles | null>(null)
  const [table, setTable] = useState<EnergyTable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sterics, setSterics] = useState<string[] | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  // D106: drawer height, pathways list, zoom (null fits the profile to the drawer), style dialog.
  const [layout, setLayout] = useState(loadLayout)
  const [zoom, setZoom] = useState<number | null>(null)
  const [styling, setStyling] = useState(false)
  const mainRef = useRef<HTMLDivElement>(null)
  const resizing = useRef<{ y: number; height: number } | null>(null)
  const panning = useRef<{ x: number; y: number; left: number; top: number } | null>(null)
  const anchor = useRef<{ fx: number; fy: number; px: number; py: number } | null>(null)
  const wheelZoom = useRef<(event: WheelEvent) => void>(() => undefined)
  const style = styleOf(settings)

  useEffect(() => {
    try {
      localStorage.setItem(LAYOUT, JSON.stringify(layout))
    } catch {
      // the browser keeps nothing; the drawer still works
    }
  }, [layout])

  /** The zoom at which the figure fills the drawer. */
  const fitScale = () => {
    const el = mainRef.current
    if (!el) return 1
    return Math.min((el.clientWidth - 32) / style.width, (el.clientHeight - 16) / style.height)
  }
  /** Zoom keeping the point under `at` (the middle by default) where it is. */
  const zoomTo = (next: number, at?: { px: number; py: number }) => {
    const el = mainRef.current
    if (el) {
      const px = at?.px ?? el.clientWidth / 2
      const py = at?.py ?? el.clientHeight / 2
      anchor.current = {
        fx: (el.scrollLeft + px) / Math.max(1, el.scrollWidth),
        fy: (el.scrollTop + py) / Math.max(1, el.scrollHeight),
        px,
        py,
      }
    }
    setZoom(clampZoom(next))
  }
  const zoomIn = () => {
    const current = zoom ?? fitScale()
    zoomTo(ZOOMS.find((z) => z > current + 1e-6) ?? 4)
  }
  const zoomOut = () => {
    const current = zoom ?? fitScale()
    zoomTo([...ZOOMS].reverse().find((z) => z < current - 1e-6) ?? 0.5)
  }
  useLayoutEffect(() => {
    const el = mainRef.current
    const at = anchor.current
    anchor.current = null
    if (!el || !at || zoom === null) return
    el.scrollLeft = at.fx * el.scrollWidth - at.px
    el.scrollTop = at.fy * el.scrollHeight - at.py
  }, [zoom])
  // Ctrl + wheel zooms about the pointer, through a listener that may cancel the browser's zoom.
  useEffect(() => {
    wheelZoom.current = (event) => {
      const el = mainRef.current
      if (!el) return
      const box = el.getBoundingClientRect()
      const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15
      zoomTo((zoom ?? fitScale()) * factor, { px: event.clientX - box.left, py: event.clientY - box.top })
    }
  })
  useEffect(() => {
    const el = mainRef.current
    if (!el || tab !== 'profile') return
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return
      event.preventDefault()
      wheelZoom.current(event)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [tab])

  const labels = useMemo(() => {
    const found = new Map<string, string>()
    for (const n of canvas.nodes) found.set(n.id, n.label || 'Untitled node')
    for (const g of canvas.groups) found.set(g.id, g.label || 'Group')
    return found
  }, [canvas])
  const branches = useMemo(() => new Map(canvas.branches.map((b) => [b.id, b])), [canvas.branches])
  const branchOf = (id: string) =>
    canvas.nodes.find((n) => n.id === id)?.branch_id ?? canvas.groups.find((g) => g.id === id)?.outgoing_branch_id ?? null

  // D81: a pathway's structures in order, each once; a group counts through its representative.
  const pathwayNodes = (ids: string[]) => {
    const found: Node[] = []
    for (const id of ids) {
      const group = canvas.groups.find((g) => g.id === id)
      const node = canvas.nodes.find((n) => n.id === (group ? group.representative_id : id))
      if (node?.xyz && !found.includes(node)) found.push(node)
    }
    return found
  }

  // Records can be deleted elsewhere; drop pathways that lost a node.
  const livePaths = paths.filter((p) => p.ids.every((id) => labels.has(id)))
  const onPaths = new Set(livePaths.flatMap((p) => p.ids))
  const reference = referenceId && onPaths.has(referenceId) ? referenceId : (livePaths[0]?.ids[0] ?? null)
  const colours = livePaths.map((p, i) => {
    const branchId = p.branchId ?? [...p.ids].reverse().map(branchOf).find((b) => b)
    return branchId ? (branches.get(branchId)?.colour ?? NO_BRANCH) : FALLBACK[i % FALLBACK.length]
  })
  const names = livePaths.map((p) => {
    const branch = p.branchId ? branches.get(p.branchId) : undefined
    return branch ? `${branch.name || 'Unnamed branch'}` : `${labels.get(p.ids[0])} → ${labels.get(p.ids.at(-1)!)}`
  })

  const pathsKey = JSON.stringify(livePaths.map((p) => ({ ids: p.ids, branch_id: p.branchId })))
  useEffect(() => {
    onPathsChange?.(JSON.parse(pathsKey) as DrawerPath[])
  }, [pathsKey, onPathsChange])

  // Every pathway, also one whose node is not on the canvas yet (still loading); the backend
  // drops those through a deleted node when the view is read again.
  const viewKey = JSON.stringify({
    tab,
    paths: paths.map((p) => ({ ids: p.ids, branch_id: p.branchId, choices: p.choices })),
  })
  useEffect(() => {
    onViewChange?.(JSON.parse(viewKey) as DrawerView)
  }, [viewKey, onViewChange])

  const request: ProfileRequest | null =
    level && livePaths.length
      ? { paths: livePaths.map((p) => p.ids), reference_id: reference, level, type, unit: settings?.energy_unit }
      : null
  const requestKey = JSON.stringify(request)

  useEffect(() => {
    const request = JSON.parse(requestKey) as ProfileRequest | null
    if (!request) return
    let current = true
    Promise.all([api.profiles(request), api.energyTable(request)]).then(
      ([p, t]) => {
        if (!current) return
        setProfiles(p)
        setTable(t)
        setError(null)
      },
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [requestKey, refreshKey])

  const replace = (index: number, next: Path | null) =>
    setPaths((current) => (next ? current.map((p, i) => (i === index ? next : p)) : current.filter((_, i) => i !== index)))

  const extend = (index: number, ids: string[], branchId: string | null) =>
    api.extendPathway(ids, branchId).then(
      (found) => replace(index, { ids: found.path, choices: found.choices, branchId }),
      (err: unknown) => setError(errorText(err)),
    )

  const startAtSelection = () => {
    if (!selectedId) return
    api.extendPathway([selectedId]).then(
      (found) => setPaths((current) => [...current, { ids: found.path, choices: found.choices, branchId: null }]),
      (err: unknown) => setError(errorText(err)),
    )
  }

  const addBranch = (branchId: string) =>
    api.branchPathway(branchId).then(
      (found) => setPaths((current) => [...current, { ids: found.path, choices: found.choices, branchId }]),
      (err: unknown) => setError(errorText(err)),
    )

  // Shown only while there is something to show; a stale result is never drawn.
  const shownProfiles = request ? profiles : null
  const shownTable = request ? table : null

  const exportSvg = () => {
    if (svgRef.current) saveProfileSvg(svgRef.current)
  }

  const exportPng = () => {
    if (svgRef.current) saveProfilePng(svgRef.current, style, () => setError('Could not export the profile image'))
  }

  const exportCsv = () => {
    if (!request) return
    api.energyTableCsv(request).then(
      (blob) => {
        const url = URL.createObjectURL(blob)
        download(url, 'energy-table.csv')
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const errorLine = error && (
    <p role="alert" className="error small">
      {error}
    </p>
  )

  return (
    <section className="drawer" aria-label="Energy drawer" style={{ height: drawerHeight(layout.height) }}>
      <div
        className="drawer-resize"
        role="separator"
        aria-orientation="horizontal"
        aria-label="Drawer height"
        title="Drag to make the drawer taller or shorter"
        tabIndex={0}
        onPointerDown={(event) => {
          resizing.current = { y: event.clientY, height: drawerHeight(layout.height) }
          event.currentTarget.setPointerCapture(event.pointerId)
        }}
        onPointerMove={(event) => {
          const start = resizing.current
          if (start) setLayout((l) => ({ ...l, height: drawerHeight(start.height + start.y - event.clientY) }))
        }}
        onPointerUp={() => (resizing.current = null)}
        onKeyDown={(event) => {
          const step = event.key === 'ArrowUp' ? 40 : event.key === 'ArrowDown' ? -40 : 0
          if (step) setLayout((l) => ({ ...l, height: drawerHeight(drawerHeight(l.height) + step) }))
        }}
      />
      <div className="drawer-head">
        <button
          className="small"
          aria-pressed={layout.pathways}
          onClick={() => setLayout((l) => ({ ...l, pathways: !l.pathways }))}
          title="Show or hide the pathways list, so the profile can take the drawer's full width"
        >
          Pathways
        </button>
        <div className="segmented" role="group" aria-label="Drawer tab">
          <button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')}>
            Energy profile
          </button>
          <button aria-pressed={tab === 'table'} onClick={() => setTab('table')}>
            Energy table
          </button>
        </div>
        <span className="muted small">
          {level ? `${energyTypeName(type, settings?.qh_temperature, settings?.qh_cutoff, settings?.standard_state)} at ${levelLabel}` : 'No energies in this investigation yet'}
        </span>
        <span className="spacer" />
        {tab === 'profile' ? (
          <>
            <div className="zoom" role="group" aria-label="Zoom">
              <button
                className="small"
                onClick={zoomOut}
                disabled={!shownProfiles}
                aria-label="Zoom out"
                title="Zoom out (Ctrl + wheel)"
              >
                −
              </button>
              <span className="muted small zoom-level" aria-label="Zoom level">
                {zoom === null ? 'Fit' : `${Math.round(zoom * 100)} %`}
              </span>
              <button
                className="small"
                onClick={zoomIn}
                disabled={!shownProfiles}
                aria-label="Zoom in"
                title="Zoom in (Ctrl + wheel)"
              >
                +
              </button>
              <button
                className="small"
                aria-pressed={zoom === null}
                onClick={() => setZoom(null)}
                disabled={!shownProfiles}
                title="Fit the profile to the drawer"
              >
                Fit
              </button>
            </div>
            <button
              className="small"
              onClick={() => setStyling(true)}
              disabled={!settings}
              title="Size, font, colours, connectors and labels of the profile and its saved images (D106)"
            >
              Style…
            </button>
            <button className="small" onClick={exportPng} disabled={!shownProfiles}>
              Save profile as PNG
            </button>
            <button className="small" onClick={exportSvg} disabled={!shownProfiles}>
              Save profile as SVG
            </button>
          </>
        ) : (
          <button className="small" onClick={exportCsv} disabled={!shownTable}>
            Export CSV
          </button>
        )}
      </div>
      <div className="drawer-body">
        {layout.pathways && (
          <div className="pathways" aria-label="Pathways">
            {livePaths.map((path, index) => (
              <div key={index} className="pathway" role="group" aria-label={`Pathway ${names[index]}`}>
                <div className="pathway-head">
                  <span className="swatch" style={{ background: colours[index] }} />
                  <strong>{names[index]}</strong>
                  <span className="spacer" />
                  <button
                    className="small"
                    disabled={path.ids.length < 2}
                    onClick={() => replace(index, { ...path, ids: path.ids.slice(0, -1), choices: [] })}
                    title="Remove the last node"
                  >
                    Undo step
                  </button>
                  {onTurnover && closesCycle(path.ids) && (
                    <button
                      className="small"
                      onClick={() => onTurnover(path.ids, names[index])}
                      title="TOF of this closed cycle from the energetic-span model (D86)"
                    >
                      Turnover
                    </button>
                  )}
                  <button
                    className="small"
                    onClick={() => setSterics(path.ids)}
                    title="Buried volume and steric maps along this pathway; a group counts through its representative (D81)"
                  >
                    Sterics
                  </button>
                  <button className="small" onClick={() => replace(index, null)} aria-label={`Remove pathway ${names[index]}`}>
                    ✕
                  </button>
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
                {closesCycle(path.ids) && <p className="muted small">Cycle closed.</p>}
                {path.choices.length > 0 && (
                  <div className="choices" role="group" aria-label="Continue with">
                    <span className="muted small">Continue with:</span>
                    {path.choices.map((choice) => (
                      <button
                        key={choice.node_id}
                        className="small"
                        onClick={() => extend(index, [...path.ids, choice.node_id], path.branchId)}
                      >
                        {choice.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))}
            <div className="pathway-add">
              <button className="small" onClick={startAtSelection} disabled={!selectedId}>
                Start at selected node
              </button>
              <select
                aria-label="Add branch pathway"
                value=""
                onChange={(event) => event.target.value && addBranch(event.target.value)}
              >
                <option value="">Add a branch…</option>
                {canvas.branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name || 'Unnamed branch'}
                  </option>
                ))}
              </select>
            </div>
            {livePaths.length > 0 && (
              <label className="field">
                <span>Reference</span>
                <select
                  aria-label="Reference node"
                  value={reference ?? ''}
                  onChange={(event) => onReference(event.target.value || null)}
                >
                  {[...onPaths].map((id) => (
                    <option key={id} value={id}>
                      {labels.get(id)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {errorLine}
          </div>
        )}
        <div
          ref={mainRef}
          className={`drawer-main${tab === 'profile' ? (zoom === null ? ' fit' : ' zoomed') : ''}`}
          onPointerDown={(event) => {
            const el = mainRef.current
            if (!el || zoom === null || tab !== 'profile' || event.button !== 0) return
            panning.current = { x: event.clientX, y: event.clientY, left: el.scrollLeft, top: el.scrollTop }
            event.currentTarget.setPointerCapture(event.pointerId)
          }}
          onPointerMove={(event) => {
            const el = mainRef.current
            const start = panning.current
            if (!el || !start) return
            el.scrollLeft = start.left - (event.clientX - start.x)
            el.scrollTop = start.top - (event.clientY - start.y)
          }}
          onPointerUp={() => (panning.current = null)}
        >
          {!layout.pathways && errorLine}
          {!livePaths.length ? (
            <p className="muted placeholder">
              Add a branch, or select a node on the canvas and start a pathway there. Pathways follow the transitions
              you drew; at a fork you choose how to go on.
            </p>
          ) : tab === 'profile' ? (
            shownProfiles && (
              <ProfileChart
                data={shownProfiles}
                colours={colours}
                names={names}
                settings={settings}
                svgRef={svgRef}
                size={zoom === null ? undefined : { width: style.width * zoom, height: style.height * zoom }}
              />
            )
          ) : (
            shownTable && (
              <table className="energy-table" aria-label="Energy table">
                <thead>
                  <tr>
                    {shownTable.columns.map((c) => (
                      <th key={c}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shownTable.rows.map((row, i) => (
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
            )
          )}
        </div>
      </div>
      {styling && settings && (
        <ProfileStyleDialog
          settings={settings}
          preview={shownProfiles ? { data: shownProfiles, colours, names } : null}
          onSave={(profile_style) => api.saveSettings({ profile_style }).then((next) => onSettings?.(next))}
          onClose={() => setStyling(false)}
        />
      )}
      {sterics && (
        <CompareStericsDialog
          nodes={pathwayNodes(sterics)}
          title="Nodes along the pathway; groups through their representatives"
          onClose={() => setSterics(null)}
        />
      )}
    </section>
  )
}
