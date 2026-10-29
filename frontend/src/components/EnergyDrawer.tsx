import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTheme } from '../theme'
import {
  api,
  energyTypeName,
  type Canvas,
  type EnergyTable,
  type EnergyType,
  type Node,
  type EnergyProfile,
  type PathChoice,
  type PathShift,
  type ProfilePath,
  type Profiles,
  type ProfileRequest,
  type Settings,
  type ViewState,
} from '../api'
import { Icon } from '../icons'
import { figureOf } from '../profileFigure'
import { saveProfilePng, saveProfileSvg, styleOf } from '../profileStyle'
import { closesCycle, csvTitle, download } from '../util'
import { EmptyState } from './EmptyState'
import { EnergyNotes } from './EnergyNotes'
import { EnergyTableView } from './EnergyTableView'
import { GModelBadge } from './GModelBadge'
import { Modal } from './Modal'
import { Menu } from './MoreMenu'
import { ProfileChart, SCREEN_TEXT } from './ProfileChart'
import { ProfileStyleDialog } from './ProfileStyleDialog'
import { CompareStericsDialog } from './Sterics'
import { useRequest, type Request } from '../commands'
import { placeText } from '../subCanvas'

const NO_BRANCH = '#98a2b3'
const FALLBACK = ['#2459c6', '#c4320a', '#079455', '#6938ef', '#b54708']

type Path = {
  ids: string[]
  choices: Omit<PathChoice, 'transition_id'>[]
  branchId: string | null
  shown: boolean
  shifts: PathShift[]
}

/** D190: a saved profile's pathways in the drawer's form; the choices are asked for (D189). */
const pathsOf = (profile: EnergyProfile | undefined): Path[] =>
  profile?.paths.map((p) => ({ ids: p.ids, choices: [], branchId: p.branch_id, shown: p.shown, shifts: p.shifts ?? [] })) ??
  []
/** D190: what is saved of the pathways; never the choices. */
const storedOf = (paths: Path[]): ProfilePath[] =>
  paths.map((p) => ({ ids: p.ids, branch_id: p.branchId, shown: p.shown, shifts: p.shifts }))

/** A pathway as the read-only copy exports it (D79, A30), with its steps moved along the axis
 * (D194; none in copies made before). */
export type DrawerPath = { ids: string[]; branch_id: string | null; shifts?: PathShift[] }

/** D194: how far one step may be moved, in columns (the backend's `energy_profiles.MAX_SHIFT`). */
const MAX_SHIFT = 20

/** D194: `path`'s shifts with the step at `at` moved `delta` columns later (earlier when negative);
 * a step is never moved before where the pathway puts it. */
function shifted(shifts: PathShift[], at: number, delta: number): PathShift[] {
  const by = Math.min(MAX_SHIFT, Math.max(0, (shifts.find((s) => s.at === at)?.by ?? 0) + delta))
  const rest = shifts.filter((s) => s.at !== at)
  return (by ? [...rest, { at, by }] : rest).sort((a, b) => a.at - b.at)
}

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
  bsse = false,
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
  asked,
}: {
  canvas: Canvas
  settings: Settings | null
  level: string | null
  levelLabel: string
  /** D161: add each node's counterpoise BSSE at the level. */
  bsse?: boolean
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
  /** D105: the tab and the open profile (D190) to start with, as the investigation remembered them. */
  saved?: DrawerView
  /** D105: the tab and the open profile, whenever they change, to be remembered. */
  onViewChange?: (view: DrawerView) => void
  /** D184: "Start energy pathway here" from a right-click menu or the palette. */
  asked?: Request | null
}) {
  const [tab, setTab] = useState<'profile' | 'table'>(saved?.tab ?? 'profile')
  // D190: the saved profiles (null while loading), the one open and its pathways.
  const [stored, setStored] = useState<EnergyProfile[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(saved?.profile_id ?? null)
  const [paths, setPaths] = useState<Path[]>([])
  const [renaming, setRenaming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  // D194: the step each pathway's Move buttons act on, by the pathway's place in `paths`.
  const [moving, setMoving] = useState<Record<number, number>>({})
  // What the open profile last saved, so a change is saved once and loading saves nothing.
  const lastSaved = useRef<string | null>(null)
  // The open profile for callbacks that finish after a render, such as a pathway being found.
  const openRef = useRef<string | null>(null)
  const saving = useRef<Promise<unknown>>(Promise.resolve())
  const [profiles, setProfiles] = useState<Profiles | null>(null)
  const [table, setTable] = useState<EnergyTable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sterics, setSterics] = useState<string[] | null>(null)
  // D106: drawer height, pathways list, zoom (null fits the profile to the drawer), style dialog.
  const [layout, setLayout] = useState(loadLayout)
  const [zoom, setZoom] = useState<number | null>(null)
  const [styling, setStyling] = useState(false)
  const mainRef = useRef<HTMLDivElement>(null)
  const resizing = useRef<{ y: number; height: number } | null>(null)
  const panning = useRef<{ x: number; y: number; left: number; top: number; moved: boolean } | null>(null)
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
  // Each pathway with its place in `paths`, its colour and its name; D190: only the ticked
  // ones are drawn, valued and exported, and a pathway keeps its colour whether shown or not.
  const listed = paths
    .map((path, at) => {
      const branchId = path.branchId ?? [...path.ids].reverse().map(branchOf).find((b) => b)
      const colour = branchId ? (branches.get(branchId)?.colour ?? NO_BRANCH) : FALLBACK[at % FALLBACK.length]
      const branch = path.branchId ? branches.get(path.branchId) : undefined
      const name = branch
        ? `${branch.name || 'Unnamed branch'}`
        : `${labels.get(path.ids[0])} → ${labels.get(path.ids.at(-1)!)}`
      return { path, at, colour, name }
    })
    .filter(({ path }) => path.ids.every((id) => labels.has(id)))
  const drawn = listed.filter(({ path }) => path.shown)
  const livePaths = drawn.map(({ path }) => path)
  const colours = drawn.map(({ colour }) => colour)
  const names = drawn.map(({ name }) => name)
  const shifts = drawn.map(({ path }) => path.shifts)
  const onPaths = new Set(livePaths.flatMap((p) => p.ids))
  const reference = referenceId && onPaths.has(referenceId) ? referenceId : (livePaths[0]?.ids[0] ?? null)

  const pathsKey = JSON.stringify(livePaths.map((p) => ({ ids: p.ids, branch_id: p.branchId, shifts: p.shifts })))
  useEffect(() => {
    onPathsChange?.(JSON.parse(pathsKey) as DrawerPath[])
  }, [pathsKey, onPathsChange])

  const viewKey = JSON.stringify({ tab, profile_id: openId })
  useEffect(() => {
    onViewChange?.(JSON.parse(viewKey) as DrawerView)
  }, [viewKey, onViewChange])

  const request: ProfileRequest | null =
    level && livePaths.length
      ? {
          paths: livePaths.map((p) => p.ids),
          reference_id: reference,
          level,
          type,
          unit: settings?.energy_unit,
          ...(bsse ? { bsse } : {}),
        }
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

  // D189: the ways on from each pathway's end are asked for again whenever its nodes or the
  // canvas change, so a step undone or an edge drawn later never leaves a pathway at a dead end.
  const graphKey = useMemo(
    () =>
      JSON.stringify([
        canvas.transitions.map((t) => [t.source_id, t.target_id]),
        canvas.groups.map((g) => [g.id, g.label, g.member_ids, g.representative_id, g.outgoing_branch_id]),
        canvas.nodes.map((n) => [n.id, n.label, n.branch_id]),
      ]),
    [canvas],
  )
  const endsKey = JSON.stringify(paths.map((p) => [p.ids, p.branchId]))
  useEffect(() => {
    const ends = JSON.parse(endsKey) as [string[], string | null][]
    let current = true
    for (const [ids, branchId] of ends) {
      if (!ids.length) continue
      api.extendPathway(ids, branchId, false).then(
        (found) => {
          if (!current) return
          const choices = JSON.stringify(found.choices)
          setPaths((now) =>
            now.map((p) =>
              p.branchId === branchId && JSON.stringify(p.ids) === JSON.stringify(ids) && JSON.stringify(p.choices) !== choices
                ? { ...p, choices: found.choices }
                : p,
            ),
          )
        },
        // A pathway through a deleted node is dropped above; nothing to offer meanwhile.
        () => undefined,
      )
    }
    return () => {
      current = false
    }
  }, [endsKey, graphKey, refreshKey])

  const replace = (index: number, next: Path | null) =>
    setPaths((current) => (next ? current.map((p, i) => (i === index ? next : p)) : current.filter((_, i) => i !== index)))

  const extend = (index: number, path: Path, ids: string[]) =>
    api.extendPathway(ids, path.branchId).then(
      (found) => replace(index, { ...path, ids: found.path, choices: found.choices }),
      (err: unknown) => setError(errorText(err)),
    )

  // D190: the saved profiles, read once each time the drawer opens; the remembered one opens,
  // else the first. Before the view is restored (no `saved`) none opens: this drawer is about
  // to be replaced, and naming a profile now could undo what is being restored.
  useEffect(() => {
    let current = true
    api.energyProfiles().then(
      (found) => {
        if (!current) return
        const open = saved ? (found.find((p) => p.id === saved.profile_id) ?? found[0]) : undefined
        setStored(found)
        show(open)
      },
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, from what the drawer started with
  }, [])

  /** Open a saved profile (none: no profile). */
  const show = (profile: EnergyProfile | undefined) => {
    const next = pathsOf(profile)
    lastSaved.current = profile ? JSON.stringify(storedOf(next)) : null
    openRef.current = profile?.id ?? null
    setOpenId(profile?.id ?? null)
    setPaths(next)
    setMoving({})
    setRenaming(null)
  }

  // D190: every change to the open profile's pathways is saved at once, one save after the
  // other, so leaving the drawer or the app loses nothing.
  const storedKey = JSON.stringify(storedOf(paths))
  useEffect(() => {
    if (!openId || lastSaved.current === null || lastSaved.current === storedKey) return
    lastSaved.current = storedKey
    const body = { paths: JSON.parse(storedKey) as ProfilePath[] }
    saving.current = saving.current
      .then(() => api.updateEnergyProfile(openId, body))
      .then(
        (updated) => setStored((current) => current?.map((p) => (p.id === updated.id ? { ...p, paths: body.paths } : p)) ?? null),
        (err: unknown) => setError(errorText(err)),
      )
  }, [openId, storedKey])

  /** D190: a new, empty profile, opened; made after any save still on its way. */
  const newProfile = (): Promise<EnergyProfile> => {
    const made = saving.current
      .then(() => api.createEnergyProfile({}))
      .then((created) => {
        setStored((current) => [...(current ?? []), created])
        show(created)
        return created
      })
    saving.current = made.catch(() => undefined)
    return made
  }

  /** Add a pathway to the open profile, making "Profile 1" first when none is open; two
   * pathways added at once share the one profile made for them. */
  const creating = useRef<Promise<unknown> | null>(null)
  const addPath = (path: Path) => {
    if (!openRef.current && !creating.current)
      creating.current = newProfile().finally(() => {
        creating.current = null
      })
    const ready = creating.current ?? Promise.resolve()
    ready.then(
      () => setPaths((current) => [...current, path]),
      (err: unknown) => setError(errorText(err)),
    )
  }

  const startAtSelection = () => {
    if (!selectedId) return
    api.extendPathway([selectedId]).then(
      (found) => addPath({ ids: found.path, choices: found.choices, branchId: null, shown: true, shifts: [] }),
      (err: unknown) => setError(errorText(err)),
    )
  }

  useRequest(asked, 'drawer', (action) => {
    if (action === 'start') startAtSelection()
  })

  const addBranch = (branchId: string) =>
    api.branchPathway(branchId).then(
      (found) => addPath({ ids: found.path, choices: found.choices, branchId, shown: true, shifts: [] }),
      (err: unknown) => setError(errorText(err)),
    )

  const openProfile = stored?.find((p) => p.id === openId)
  const rename = (name: string) => {
    if (!openProfile) return
    if (name.trim() === openProfile.name) return setRenaming(null)
    api.updateEnergyProfile(openProfile.id, { name }).then(
      (updated) => {
        setStored((current) => current?.map((p) => (p.id === updated.id ? { ...p, name: updated.name } : p)) ?? null)
        setRenaming(null)
        setError(null)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }
  const duplicateProfile = () => {
    if (!openProfile) return
    // The copy is made from what was saved last, after any save still on its way.
    saving.current = saving.current.then(() =>
      api.duplicateEnergyProfile(openProfile.id).then(
        (copy) => {
          setStored((current) => [...(current ?? []), copy])
          show(copy)
        },
        (err: unknown) => setError(errorText(err)),
      ),
    )
  }
  const deleteProfile = () => {
    if (!openProfile) return
    const gone = openProfile.id
    saving.current = saving.current.then(() =>
      api.deleteEnergyProfile(gone).then(
        () => {
          const rest = (stored ?? []).filter((p) => p.id !== gone)
          setStored(rest)
          show(rest[0])
          setDeleting(false)
        },
        (err: unknown) => {
          setDeleting(false)
          setError(errorText(err))
        },
      ),
    )
  }

  // Shown only while there is something to show; a stale result is never drawn.
  const shownProfiles = request ? profiles : null
  const shownTable = request ? table : null

  // D190: which saved profile the drawer shows, and its New, Rename, Duplicate and Delete.
  const profilePicker = (
    <div className="profile-picker" role="group" aria-label="Saved profiles">
      {renaming !== null ? (
        <input
          aria-label="Profile name"
          value={renaming}
          maxLength={200}
          autoFocus
          onChange={(event) => setRenaming(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') rename(renaming)
            if (event.key === 'Escape') setRenaming(null)
          }}
          onBlur={() => rename(renaming)}
        />
      ) : (
        <select
          aria-label="Open profile"
          value={openId ?? ''}
          disabled={!stored?.length}
          onChange={(event) => show(stored?.find((p) => p.id === event.target.value))}
        >
          {!stored?.length && <option value="">No saved profiles</option>}
          {stored?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      )}
      <button
        className="small"
        onClick={() => newProfile().catch((err: unknown) => setError(errorText(err)))}
        title="A new, empty profile; every profile is saved as you go (D190)"
      >
        New
      </button>
      <Menu
        label="Profile actions"
        buttonClass="small"
        buttonLabel="Profile actions"
        items={[
          { label: 'Rename', onSelect: () => openProfile && setRenaming(openProfile.name), disabled: !openProfile },
          { label: 'Duplicate', onSelect: duplicateProfile, disabled: !openProfile },
          { label: 'Delete…', onSelect: () => setDeleting(true), disabled: !openProfile, danger: true },
        ]}
      >
        ⋯
      </Menu>
    </div>
  )

  const dark = useTheme() === 'dark' // D185: on screen only; the saved figure stays light
  // D181: the saved figure is drawn afresh in the style alone, without the screen's text scale,
  // zoom or pointer marks.
  const figure = () => shownProfiles && figureOf({ data: shownProfiles, colours, names, settings, shifts })

  const exportSvg = () => {
    const svg = figure()
    if (svg) saveProfileSvg(svg)
  }

  const exportPng = () => {
    const svg = figure()
    if (svg) saveProfilePng(svg, style, () => setError('Could not export the profile image'))
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
          <Icon name="panel" />
          Pathways
        </button>
        <div className="segmented" role="group" aria-label="Drawer tab">
          <button aria-pressed={tab === 'profile'} onClick={() => setTab('profile')} title="Energy profile">
            Profile
          </button>
          <button aria-pressed={tab === 'table'} onClick={() => setTab('table')} title="Energy table">
            Table
          </button>
        </div>
        <span className="muted small">
          {level
            ? `${energyTypeName(type, settings?.qh_temperature, settings?.qh_cutoff, settings?.standard_state, shownProfiles?.job_temperatures)} at ${levelLabel}`
            : 'No energies in this investigation yet'}
        </span>
        {type === 'G' && <GModelBadge models={shownProfiles?.g_models} />}
        <span className="spacer" />
        {tab === 'profile' && (
          // D181: one group: out, the level (Fit, or the zoom, which goes back to Fit), in.
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
            <button
              className="small zoom-level"
              onClick={() => (zoom === null ? zoomTo(1) : setZoom(null))}
              disabled={!shownProfiles}
              title={zoom === null ? 'Fitted to the drawer; click for 100 %' : 'Fit the profile to the drawer'}
            >
              {zoom === null ? 'Fit' : `${Math.round(zoom * 100)} %`}
            </button>
            <button
              className="small"
              onClick={zoomIn}
              disabled={!shownProfiles}
              aria-label="Zoom in"
              title="Zoom in (Ctrl + wheel)"
            >
              +
            </button>
          </div>
        )}
        {tab === 'profile' && (
          <button
            className="small"
            onClick={() => setStyling(true)}
            disabled={!settings}
            title="Size, font, colours, connectors and labels of the profile and its saved images (D106)"
          >
            Style…
          </button>
        )}
        <Menu
          label="Export"
          buttonClass="small"
          buttonLabel="Export ▾"
          title="The profile as PNG or SVG, the table as CSV"
          items={[
            {
              label: 'Profile as PNG',
              onSelect: exportPng,
              disabled: !shownProfiles,
              title: 'At the size and resolution of the profile style (Style…)',
            },
            { label: 'Profile as SVG', onSelect: exportSvg, disabled: !shownProfiles },
            { label: 'Table as CSV', onSelect: exportCsv, disabled: !shownTable, title: csvTitle(type) },
          ]}
        >
          <Icon name="download" />
          Export
          <Icon name="chevron" />
        </Menu>
      </div>
      <div className="drawer-body">
        {layout.pathways && (
          <div className="pathways" aria-label="Pathways">
            {profilePicker}
            {listed.map(({ path, at: index, colour, name }) => (
              <div
                key={index}
                className={`pathway${path.shown ? '' : ' hidden-path'}`}
                role="group"
                aria-label={`Pathway ${name}`}
              >
                <div className="pathway-head">
                  <input
                    type="checkbox"
                    checked={path.shown}
                    onChange={(event) => replace(index, { ...path, shown: event.target.checked })}
                    aria-label={`Show ${name} on the figure`}
                    title="Show on the figure; any number of pathways may be drawn together (D190)"
                  />
                  <span className="swatch" style={{ background: colour }} />
                  <strong>{name}</strong>
                  <span className="spacer" />
                  <button
                    className="small"
                    disabled={path.ids.length < 2}
                    onClick={() =>
                      replace(index, {
                        ...path,
                        ids: path.ids.slice(0, -1),
                        choices: [],
                        shifts: path.shifts.filter((shift) => shift.at < path.ids.length - 1),
                      })
                    }
                    title="Remove the last node; every way on from the node before is offered again (D189)"
                  >
                    Undo step
                  </button>
                  {onTurnover && closesCycle(path.ids) && (
                    <button
                      className="small"
                      onClick={() => onTurnover(path.ids, name)}
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
                  <button className="small" onClick={() => replace(index, null)} aria-label={`Remove pathway ${name}`}>
                    ✕
                  </button>
                </div>
                <p className="pathway-nodes">
                  {path.ids.map((id, i) => (
                    <span key={i}>
                      {i > 0 && ' → '}
                      {path.shifts.some((shift) => shift.at === i) && (
                        <span className="shift-mark" title="Moved later along the axis, with the steps after it (D194)">
                          {`+${path.shifts.find((shift) => shift.at === i)?.by} `}
                        </span>
                      )}
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
                {path.ids.length > 0 &&
                  (() => {
                    // D194: one step, and with it every step after it, moved along the axis.
                    const at = Math.min(moving[index] ?? 0, path.ids.length - 1)
                    const by = path.shifts.find((shift) => shift.at === at)?.by ?? 0
                    const move = (delta: number) => replace(index, { ...path, shifts: shifted(path.shifts, at, delta) })
                    return (
                      <div className="pathway-move" role="group" aria-label={`Move ${name} along the axis`}>
                        <span className="muted small">Move</span>
                        <select
                          aria-label="Step to move"
                          value={at}
                          onChange={(event) => setMoving((m) => ({ ...m, [index]: Number(event.target.value) }))}
                          title="This step and every step after it move; the steps before stay put"
                        >
                          {path.ids.map((id, i) => {
                            const moved = path.shifts.find((shift) => shift.at === i)?.by
                            return (
                              <option key={i} value={i}>
                                {`${i + 1}. ${labels.get(id)}${moved ? ` (+${moved})` : ''}`}
                              </option>
                            )
                          })}
                        </select>
                        <button
                          className="small"
                          disabled={by === 0}
                          onClick={() => move(-1)}
                          aria-label="Move back"
                          title="Move this step and the ones after it one column back"
                        >
                          ←
                        </button>
                        <button
                          className="small"
                          disabled={by >= MAX_SHIFT}
                          onClick={() => move(1)}
                          aria-label="Move on"
                          title="Move this step and the ones after it one column further along"
                        >
                          →
                        </button>
                        {path.shifts.length > 0 && (
                          <button
                            className="small quiet"
                            onClick={() => replace(index, { ...path, shifts: [] })}
                            title="Put every step of this pathway back where the pathway places it"
                          >
                            Reset
                          </button>
                        )}
                      </div>
                    )
                  })()}
                {path.choices.length > 0 && (
                  <div className="choices" role="group" aria-label="Continue with">
                    <span className="muted small">Continue with:</span>
                    {path.choices.map((choice) => (
                      <button
                        key={choice.node_id}
                        className="small"
                        onClick={() => extend(index, path, [...path.ids, choice.node_id])}
                        title={
                          choice.kind === 'group'
                            ? 'The group, valued by its representative'
                            : choice.group_label
                              ? `${choice.label}, a member of ${choice.group_label}`
                              : undefined
                        }
                      >
                        {choice.label}
                        {choice.kind === 'group' && <span className="muted"> (group)</span>}
                        {choice.group_label && <span className="muted"> in {choice.group_label}</span>}
                        {/* D193: where it is, when inside a sub-investigation */}
                        {placeText(canvas, choice.node_id) && (
                          <span className="muted"> {placeText(canvas, choice.node_id)}</span>
                        )}
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
            panning.current = { x: event.clientX, y: event.clientY, left: el.scrollLeft, top: el.scrollTop, moved: false }
          }}
          onPointerMove={(event) => {
            const el = mainRef.current
            const start = panning.current
            if (!el || !start) return
            // Held only once it moves, so a click still reaches a level on the chart (D181).
            if (!start.moved && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 4) return
            if (!start.moved) event.currentTarget.setPointerCapture(event.pointerId)
            start.moved = true
            el.scrollLeft = start.left - (event.clientX - start.x)
            el.scrollTop = start.top - (event.clientY - start.y)
          }}
          onPointerUp={() => (panning.current = null)}
        >
          {!layout.pathways && errorLine}
          {livePaths.length > 0 && <EnergyNotes notes={shownProfiles?.notes} />}
          {!livePaths.length && listed.length ? (
            <EmptyState compact icon="chart" title="No pathway shown">
              Tick “Show on the figure” beside a pathway to draw it; any number can be drawn together.
            </EmptyState>
          ) : !livePaths.length ? (
            <EmptyState
              compact
              icon="chart"
              title="No pathways yet"
              actions={
                <>
                  {canvas.branches
                    .filter((b) => canvas.nodes.some((n) => n.branch_id === b.id))
                    .slice(0, 3)
                    .map((b, index) => (
                      <button key={b.id} className={index === 0 ? 'primary' : undefined} onClick={() => addBranch(b.id)}>
                        <Icon name="branch" />
                        Pathway of {b.name || 'Unnamed branch'}
                      </button>
                    ))}
                  <button
                    onClick={startAtSelection}
                    disabled={!selectedId}
                    title={selectedId ? undefined : 'Select a node on the canvas first'}
                  >
                    <Icon name="target" />
                    Start at the selected node
                  </button>
                </>
              }
            >
              A pathway follows the transitions you drew and shows them as an energy profile and a table. Add a
              branch&apos;s pathway, or select a node on the canvas and start one there.
            </EmptyState>
          ) : tab === 'profile' ? (
            shownProfiles && (
              <ProfileChart
                data={shownProfiles}
                colours={colours}
                names={names}
                shifts={shifts}
                settings={settings}
                size={zoom === null ? undefined : { width: style.width * zoom, height: style.height * zoom }}
                textScale={SCREEN_TEXT}
                dark={dark}
                pointing={{
                  selectedId,
                  onSelect: onSelectNode,
                  branchName: (id) => {
                    const branch = branches.get(id)
                    return branch && (branch.name || 'Unnamed branch')
                  },
                }}
              />
            )
          ) : (
            shownTable && (
              <EnergyTableView
                table={shownTable}
                type={type}
                reference={reference ? (labels.get(reference) ?? null) : null}
                colourOf={(id) => branches.get(id)?.colour}
                selectedId={selectedId}
              />
            )
          )}
        </div>
      </div>
      {deleting && openProfile && (
        <Modal
          title="Delete profile"
          onClose={() => setDeleting(false)}
          actions={
            <>
              <button onClick={() => setDeleting(false)}>Cancel</button>
              <button className="danger" onClick={deleteProfile}>
                Delete
              </button>
            </>
          }
        >
          <p>Delete the profile “{openProfile.name}” and its pathways? The nodes and their energies are kept.</p>
        </Modal>
      )}
      {styling && settings && (
        <ProfileStyleDialog
          settings={settings}
          preview={shownProfiles ? { data: shownProfiles, colours, names, shifts } : null}
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
