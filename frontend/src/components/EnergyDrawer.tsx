import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTheme } from '../theme'
import {
  api,
  captureInvestigation,
  energyTypeName,
  type Canvas,
  type EnergyTable,
  type EnergyType,
  type Node,
  type EnergyProfile,
  type PathAlternative,
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
import {
  pathwayColours,
  pathwayLabels,
  pathwayLegend,
  profilePngBlob,
  profileSvgBlob,
  saveProfilePng,
  saveProfileSvg,
  styleOf,
} from '../profileStyle'
import { closesCycle, csvTitle, downloadBlob, errorText, fileName } from '../util'
import { EmptyState } from './EmptyState'
import { EnergyNotes } from './EnergyNotes'
import { EnergyTableView } from './EnergyTableView'
import { GModelBadge } from './GModelBadge'
import { Modal } from './Modal'
import { ContextMenu, type MenuHeading } from './ContextMenu'
import { Menu, type MenuItem } from './MoreMenu'
import { ProfileChart, SCREEN_TEXT } from './ProfileChart'
import { ProfileStyleDialog } from './ProfileStyleDialog'
import { PathwayColour } from './PathwayColour'
import { CompareStericsDialog } from './Sterics'
import { useRequest, type Request } from '../commands'
import { placeText } from '../subCanvas'


type Choice = Omit<PathChoice, 'transition_id'>
type Path = {
  ids: string[]
  /** The ways on from the last node, and (D228) the ways in to the first. */
  choices: Choice[]
  before: Choice[]
  branchId: string | null
  shown: boolean
  shifts: PathShift[]
  /** D195: the colour the user chose; null: the branch's. */
  colour: string | null
}

/** D190: a saved profile's pathways in the drawer's form; the choices are asked for (D189).
 * D221: those that lost a node come last; they are listed, not drawn, and saved as they are. */
const pathsOf = (profile: EnergyProfile | undefined): Path[] =>
  [...(profile?.paths ?? []), ...(profile?.lost ?? [])].map((p) => ({
    ids: p.ids,
    choices: [],
    before: [],
    branchId: p.branch_id,
    shown: p.shown,
    shifts: p.shifts ?? [],
    colour: p.colour ?? null,
  }))
/** D190: what is saved of the pathways; never the choices. */
const storedOf = (paths: Path[]): ProfilePath[] =>
  paths.map((p) => ({ ids: p.ids, branch_id: p.branchId, shown: p.shown, shifts: p.shifts, colour: p.colour }))

/** A pathway as the read-only copy exports it (D79, A30), with its steps moved along the axis
 * (D194) and the colour chosen for it (D195); neither in copies made before. */
export type DrawerPath = { ids: string[]; branch_id: string | null; shifts?: PathShift[]; colour?: string | null }

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
  onSelectNode,
  onSettings,
  onPathsChange,
  onTurnover,
  saved,
  onViewChange,
  asked,
  onFigure,
}: {
  canvas: Canvas
  settings: Settings | null
  level: string | null
  levelLabel: string
  /** D161: add each node's counterpoise BSSE at the level. */
  bsse?: boolean
  type: EnergyType
  /** The node cards' reference: a profile's first reference when it is on its pathways (D205). */
  referenceId: string | null
  selectedId: string | null
  refreshKey: number
  onSelectNode: (id: string) => void
  /** D106: the app's settings after the profile style was saved. */
  onSettings?: (settings: Settings) => void
  /** The pathways shown and the open profile's reference (D205), for the read-only copy (D79). */
  onPathsChange?: (paths: DrawerPath[], referenceId: string | null) => void
  /** D86: a saved turnover from a closed pathway, opened in the Analyses view. */
  onTurnover?: (ids: string[], name: string) => void
  /** D105: the tab and the open profile (D190) to start with, as the investigation remembered them. */
  saved?: DrawerView
  /** D105: the tab and the open profile, whenever they change, to be remembered. */
  onViewChange?: (view: DrawerView) => void
  /** D184: "Start energy pathway here" from a right-click menu or the palette. */
  asked?: Request | null
  /** D226: the profile figure that was asked for (`figure:svg`, `figure:png`), or why not. */
  onFigure?: (figure: { name: string; blob: Blob } | { error: string }) => void
}) {
  const [tab, setTab] = useState<'profile' | 'table'>(saved?.tab ?? 'profile')
  // D190: the saved profiles (null while loading), the one open and its pathways.
  const [stored, setStored] = useState<EnergyProfile[] | null>(null)
  const [openId, setOpenId] = useState<string | null>(saved?.profile_id ?? null)
  const [paths, setPaths] = useState<Path[]>([])
  // D205: the open profile's own reference, shared by all its pathways; not the node cards'.
  const [profileRef, setProfileRef] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  // D194, D228: the step whose Move buttons are out, by its pathway's place in `paths` and its
  // own place on the pathway; they open from the step's menu.
  const [moving, setMoving] = useState<{ index: number; at: number } | null>(null)
  // D228: the menu of one step of a pathway, below its name. `key` is the pathway's nodes and
  // the step when it opened; `swaps` is what the step can be swapped for, once it is known.
  const [stepMenu, setStepMenu] = useState<{
    x: number
    y: number
    index: number
    at: number
    key: string
    swaps: PathAlternative[] | null
  } | null>(null)
  // D221: what the list last did on its own account: a pathway removed (which Undo puts back
  // where it was) or one not added because the profile has it already.
  const [notice, setNotice] = useState<{ text: string; undo?: { path: Path; at: number } } | null>(null)
  // The pathways as last drawn, for a pathway found after a render to be compared with.
  const pathsRef = useRef<Path[]>([])
  // What the open profile last saved, so a change is saved once and loading saves nothing.
  const lastSaved = useRef<string | null>(null)
  // The open profile for callbacks that finish after a render, such as a pathway being found.
  const openRef = useRef<string | null>(null)
  const saving = useRef<Promise<unknown>>(Promise.resolve())
  const inInvestigation = useRef(captureInvestigation()).current
  const [result, setResult] = useState<{
    key: string
    request: string
    profiles?: Profiles
    table?: EnergyTable
    error?: string
  } | null>(null)
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

  const labels = useMemo(() => pathwayLabels(canvas), [canvas])
  const branches = useMemo(() => new Map(canvas.branches.map((b) => [b.id, b])), [canvas.branches])

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

  // Records can be deleted elsewhere; a pathway that lost a node is set apart (`lost`, D221).
  // Each pathway with its place in `paths`, its colour and its name; D190: only the ticked
  // ones are drawn, valued and exported, and a pathway keeps its colour whether shown or not.
  pathsRef.current = paths
  const named = paths.map((path, at) => ({
    path,
    at,
    ...pathwayLegend(canvas, branches, labels, path.ids, path.branchId, at),
  }))
  const listed = named.filter(({ path }) => path.ids.every((id) => labels.has(id)))
  // D221: a pathway through a node deleted since: listed with what is left of it, never drawn.
  const lost = named.filter(({ path }) => !path.ids.every((id) => labels.has(id)))
  const drawn = listed.filter(({ path }) => path.shown)
  const livePaths = drawn.map(({ path }) => path)
  const colours = drawn.map(({ colour }) => colour)
  const names = drawn.map(({ name }) => name)
  const shifts = drawn.map(({ path }) => path.shifts)
  const chosen = drawn.map(({ path }) => path.colour)
  // D195: each pathway's swatch in the colour it is drawn in (a hidden one: as if alone).
  const drawnColours = pathwayColours(colours, style, chosen)
  const swatchOf = (at: number, colour: string, path: Path) => {
    const i = drawn.findIndex((d) => d.at === at)
    return i >= 0 ? drawnColours[i] : (path.colour ?? pathwayColours([colour], style)[0])
  }
  const onPaths = new Set(livePaths.flatMap((p) => p.ids))
  // D205: the profile's reference while it is on a pathway in it; one on a hidden pathway is
  // kept for when that is shown again. Without one: the cards' reference when it is on a shown
  // pathway, else the first node shown, which then becomes the profile's own.
  const kept = profileRef && paths.some((p) => p.ids.includes(profileRef)) ? profileRef : null
  const reference =
    kept && onPaths.has(kept)
      ? kept
      : !kept && referenceId && onPaths.has(referenceId)
        ? referenceId
        : (livePaths[0]?.ids[0] ?? null)
  useEffect(() => {
    if (openId && !kept && reference) setProfileRef(reference)
  }, [openId, kept, reference])

  const pathsKey = JSON.stringify({
    paths: livePaths.map((p) => ({ ids: p.ids, branch_id: p.branchId, shifts: p.shifts, colour: p.colour })),
    reference,
  })
  useEffect(() => {
    const shown = JSON.parse(pathsKey) as { paths: DrawerPath[]; reference: string | null }
    onPathsChange?.(shown.paths, shown.reference)
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
  // INV-5: the refresh belongs to the result too: changed records invalidate old numbers.
  const resultKey = JSON.stringify([requestKey, refreshKey])
  const currentResultKey = useRef(resultKey)
  currentResultKey.current = resultKey

  useEffect(() => {
    const request = JSON.parse(requestKey) as ProfileRequest | null
    if (!request) return
    let current = true
    Promise.all([api.profiles(request), api.energyTable(request)]).then(
      ([p, t]) => {
        if (!current) return
        setResult({ key: resultKey, request: requestKey, profiles: p, table: t })
        // As before #179: a fetch that succeeds clears an earlier drawer error (a failed
        // save, export or "Continue with"), so it does not stay on screen for good.
        setError(null)
      },
      (err: unknown) => current && setResult({ key: resultKey, request: requestKey, error: errorText(err) }),
    )
    return () => {
      current = false
    }
  }, [requestKey, resultKey])

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
          const before = found.before ?? []
          const ways = JSON.stringify([found.choices, before])
          setPaths((now) =>
            now.map((p) =>
              p.branchId === branchId &&
              JSON.stringify(p.ids) === JSON.stringify(ids) &&
              JSON.stringify([p.choices, p.before]) !== ways
                ? { ...p, choices: found.choices, before }
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

  /** D221: take a pathway out of the profile; Undo puts it back where it was. */
  const remove = (index: number, path: Path, text: string) => {
    setNotice({ text, undo: { path, at: index } })
    setMoving(null)
    replace(index, null)
  }
  const restore = (undo: { path: Path; at: number }) => {
    setPaths((current) => [...current.slice(0, undo.at), undo.path, ...current.slice(undo.at)])
    setMoving(null)
    setNotice(null)
  }

  /** Go on to `ids`; with `follow`, as far as one arrow leads on (D189). A step taken against
   * its arrow is one step: the walk only ever follows arrows (D228). */
  const extend = (index: number, path: Path, ids: string[], follow = true) =>
    api.extendPathway(ids, path.branchId, follow).then(
      (found) => replace(index, { ...path, ids: found.path, choices: found.choices, before: found.before ?? [] }),
      (err: unknown) => setError(errorText(err)),
    )

  /** D228: put `id` before the first node; with `follow`, as far back as one arrow leads in.
   * The steps already there keep their moves along the axis (D194), counted from the new start. */
  const extendBack = (index: number, path: Path, id: string, follow = true) =>
    api.extendPathway([id, ...path.ids], path.branchId, follow, true).then(
      (found) => {
        const added = found.path.length - path.ids.length
        setMoving(null)
        replace(index, {
          ...path,
          ids: found.path,
          choices: found.choices,
          before: found.before ?? [],
          shifts: path.shifts.map((shift) => ({ ...shift, at: shift.at + added })),
        })
      },
      (err: unknown) => setError(errorText(err)),
    )

  /** D228: open the menu of the step at `at` of a pathway, below its name in the list, and ask
   * what it can be swapped for. */
  const openStep = (element: HTMLElement, index: number, at: number, path: Path) => {
    const box = element.getBoundingClientRect()
    const key = JSON.stringify([path.ids, at])
    setStepMenu({ x: box.left, y: box.bottom + 2, index, at, key, swaps: null })
    const known = (swaps: PathAlternative[]) =>
      setStepMenu((menu) => (menu && menu.index === index && menu.key === key ? { ...menu, swaps } : menu))
    api.pathwayAlternatives(path.ids, at).then(known, () => known([]))
  }

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
    const ref = profile?.reference_id ?? null
    lastSaved.current = profile ? JSON.stringify({ paths: storedOf(next), reference_id: ref }) : null
    openRef.current = profile?.id ?? null
    setOpenId(profile?.id ?? null)
    setPaths(next)
    setProfileRef(ref)
    setMoving(null)
    setStepMenu(null)
    setRenaming(null)
    setNotice(null)
  }

  // D190: every change to the open profile's pathways (and its reference, D205) is saved at
  // once, one save after the other, so leaving the drawer or the app loses nothing.
  const storedKey = JSON.stringify({ paths: storedOf(paths), reference_id: kept })
  useEffect(() => {
    if (!openId || lastSaved.current === null || lastSaved.current === storedKey) return
    lastSaved.current = storedKey
    const body = JSON.parse(storedKey) as { paths: ProfilePath[]; reference_id: string | null }
    // The kept copy takes the change at once, not when the save comes back: a profile opened
    // again before then must open as it was left, not as it was last saved.
    // D221: its pathways that lost a node are among `body.paths` from now on.
    setStored((current) => current?.map((p) => (p.id === openId ? { ...p, ...body, lost: [] } : p)) ?? null)
    saving.current = saving.current
      .then(() => inInvestigation(() => api.updateEnergyProfile(openId, body)))
      .then(
        () => undefined,
        (err: unknown) => setError(errorText(err)),
      )
    // `inInvestigation` is the same function on every render (held in a ref), so it never re-runs this.
  }, [openId, storedKey, inInvestigation])

  // A profile being made (New, or the first pathway with none open): a pathway found
  // meanwhile joins it, not the profile open before it, which it is about to replace.
  const creating = useRef<Promise<unknown> | null>(null)
  /** D190: a new, empty profile, opened; made after any save still on its way. */
  const newProfile = (): Promise<EnergyProfile> => {
    const made = saving.current
      .then(() => inInvestigation(() => api.createEnergyProfile({})))
      .then((created) => {
        setStored((current) => [...(current ?? []), created])
        show(created)
        return created
      })
    saving.current = made.catch(() => undefined)
    creating.current = made
    const done = () => {
      if (creating.current === made) creating.current = null
    }
    made.then(done, done)
    return made
  }

  /** Add a pathway to the open profile, or to the one being made; "Profile 1" is made first
   * when none is open, and two pathways added at once share the one profile made for them. */
  const addPath = (path: Path) => {
    if (!openRef.current && !creating.current) void newProfile().catch(() => undefined)
    // A profile being made is empty, and `pathsRef` still holds the one open before it.
    const made = creating.current !== null
    const ready = creating.current ?? Promise.resolve()
    ready.then(
      () => {
        // D221: the same nodes in the same order would be drawn on top of the pathway already
        // there; that one is shown instead, and the list says so.
        const key = JSON.stringify(path.ids)
        const same = made ? -1 : pathsRef.current.findIndex((p) => JSON.stringify(p.ids) === key)
        if (same < 0) {
          setNotice(null)
          return setPaths((current) => [...current, path])
        }
        const { name } = pathwayLegend(canvas, branches, labels, path.ids, pathsRef.current[same].branchId, same)
        setNotice({ text: `“${name}” is already in this profile.` })
        setPaths((current) =>
          current.map((p) => (JSON.stringify(p.ids) === key && !p.shown ? { ...p, shown: true } : p)),
        )
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const startAtSelection = () => {
    if (!selectedId) return
    api.extendPathway([selectedId]).then(
      (found) =>
        addPath({
          ids: found.path,
          choices: found.choices,
          before: found.before ?? [],
          branchId: null,
          shown: true,
          shifts: [],
          colour: null,
        }),
      (err: unknown) => setError(errorText(err)),
    )
  }

  // D226: a figure Claude asked for is made once the profile's numbers are in.
  const [figureAsked, setFigureAsked] = useState<'svg' | 'png' | null>(null)
  useRequest(asked, 'drawer', (action) => {
    if (action === 'start') startAtSelection()
    else if (action === 'figure:svg' || action === 'figure:png') setFigureAsked(action === 'figure:svg' ? 'svg' : 'png')
  })

  const addBranch = (branchId: string) =>
    api.branchPathway(branchId).then(
      (found) =>
        addPath({
          ids: found.path,
          choices: found.choices,
          before: found.before ?? [],
          branchId,
          shown: true,
          shifts: [],
          colour: null,
        }),
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
      inInvestigation(() => api.duplicateEnergyProfile(openProfile.id)).then(
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
      inInvestigation(() => api.deleteEnergyProfile(gone)).then(
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

  // A result for another request (type, level, reference, pathways, BSSE) is never drawn.
  // While the same request is only being fetched again after a record changed (refreshKey),
  // the last result stays up rather than flashing "Loading…" on every edit; exports wait for
  // the refreshed numbers (INV-5).
  const shownResult = request && result?.request === requestKey ? result : null
  const fresh = shownResult !== null && result?.key === resultKey
  const shownProfiles = shownResult?.profiles ?? null
  const shownTable = shownResult?.table ?? null
  const exportProfiles = fresh ? shownProfiles : null
  const exportTable = fresh ? shownTable : null

  /** What says which choice this is: a group, or the group a node is in (D108). */
  const kindOf = (choice: { kind?: string; group_label?: string | null }) =>
    choice.kind === 'group' ? 'group' : choice.group_label ? `in ${choice.group_label}` : undefined

  /** The ways on ("Continue with") or in ("Start before", D228) along the arrows, one button
   * each. Those against an arrow are in the menu of the first and the last step instead. */
  const choiceRow = (label: string, choices: Choice[], take: (choice: Choice) => void) => {
    const along = choices.filter((choice) => !choice.against)
    if (!along.length) return null
    return (
      <div className="choices" role="group" aria-label={label}>
        <span className="muted small">{label}:</span>
        {along.map((choice) => (
          <button
            key={choice.node_id}
            className="small"
            onClick={() => take(choice)}
            title={
              choice.kind === 'group'
                ? 'The group, valued by its representative; a member is taken by swapping the step afterwards'
                : choice.group_label
                  ? `${choice.label}, a member of ${choice.group_label}`
                  : undefined
            }
          >
            {choice.label}
            {choice.kind === 'group' && <span className="muted"> (group)</span>}
            {choice.group_label && <span className="muted"> in {choice.group_label}</span>}
            {/* D193: where it is, when inside a sub-investigation */}
            {placeText(canvas, choice.node_id) && <span className="muted"> {placeText(canvas, choice.node_id)}</span>}
          </button>
        ))}
      </div>
    )
  }

  /** D228: the menu of one step of a pathway; null once the pathway has changed under it. */
  const stepContent = (): { label: string; heading: MenuHeading; items: MenuItem[] } | null => {
    if (!stepMenu) return null
    const entry = listed.find(({ at }) => at === stepMenu.index)
    if (!entry || JSON.stringify([entry.path.ids, stepMenu.at]) !== stepMenu.key) return null
    const { path, name } = entry
    const { index, at, swaps } = stepMenu
    const last = path.ids.length - 1
    const cycle = closesCycle(path.ids)
    const by = path.shifts.find((shift) => shift.at === at)?.by ?? 0
    const label = labels.get(path.ids[at]) ?? ''
    const change = (next: Partial<Path>) => {
      setMoving(null)
      replace(index, { ...path, choices: [], before: [], ...next })
    }
    const against = (choices: Choice[], take: (choice: Choice) => void): MenuItem[] =>
      choices
        .filter((choice) => choice.against)
        .map((choice) => ({ label: choice.label, aside: kindOf(choice), onSelect: () => take(choice) }))
    const before = at === 0 ? against(path.before, (choice) => extendBack(index, path, choice.node_id, false)) : []
    const after =
      at === last ? against(path.choices, (choice) => extend(index, path, [...path.ids, choice.node_id], false)) : []
    // The step's own group is said in the heading; only another group is named beside a swap.
    const group = canvas.groups.find((g) => g.id === path.ids[at] || g.member_ids.includes(path.ids[at]))
    const own = group ? labels.get(group.id) : undefined
    const items: MenuItem[] = [
      {
        label: 'Swap for',
        onSelect: () => undefined,
        items: swaps?.length
          ? swaps.map((swap) => ({
              label: swap.label,
              aside: swap.kind === 'node' && swap.group_label === own ? undefined : kindOf(swap),
              onSelect: () => change({ ids: swap.path }),
            }))
          : undefined,
        disabled: !swaps?.length,
        aside: swaps === null ? '…' : undefined,
        title:
          swaps === null
            ? 'Looking for what fits here'
            : swaps.length
              ? 'Another conformer, the group, or any node the arrows join to the steps before and after'
              : 'Nothing else is joined to the steps before and after this one',
      },
      {
        label: 'Branch off here',
        disabled: at === last,
        title:
          at === last
            ? 'This is the last step: take a way on first, then branch off here to take another'
            : 'A new pathway with the steps up to this one, to go another way on from here',
        onSelect: () =>
          addPath({
            ids: path.ids.slice(0, at + 1),
            choices: [],
            before: [],
            branchId: null,
            shown: true,
            shifts: path.shifts.filter((shift) => shift.at <= at),
            colour: null,
          }),
      },
      {
        label: cycle ? 'Start the cycle here' : 'Start the pathway here',
        separated: true,
        disabled: at === 0 || (cycle && at === last),
        title: cycle
          ? 'The same cycle, drawn from this step round to it again'
          : 'Drop the steps before this one',
        onSelect: () =>
          cycle
            ? change({ ids: [...path.ids.slice(at, last), ...path.ids.slice(0, at), path.ids[at]], shifts: [] })
            : change({
                ids: path.ids.slice(at),
                shifts: path.shifts
                  .filter((shift) => shift.at > at)
                  .map((shift) => ({ ...shift, at: shift.at - at })),
              }),
      },
      {
        label: 'End the pathway here',
        disabled: at === last,
        title: 'Drop the steps after this one; every way on from here is offered again',
        onSelect: () =>
          change({ ids: path.ids.slice(0, at + 1), shifts: path.shifts.filter((shift) => shift.at <= at) }),
      },
      ...(before.length
        ? [
            {
              label: 'Start before, against an arrow',
              onSelect: () => undefined,
              items: before,
              title: 'A node this one has an arrow to: the pathway follows that arrow backwards',
            },
          ]
        : []),
      ...(after.length
        ? [
            {
              label: 'Continue, against an arrow',
              onSelect: () => undefined,
              items: after,
              title: 'A node with an arrow to this one: the pathway follows that arrow backwards',
            },
          ]
        : []),
      {
        label: 'Move along the axis',
        separated: true,
        aside: by ? `+${by}` : undefined,
        title: 'Move this step, and every step after it, later along the axis of the figure (D194)',
        onSelect: () => setMoving({ index, at }),
      },
      ...(path.shifts.length
        ? [
            {
              label: 'Put every step back',
              title: 'Put every step of this pathway back where the pathway places it',
              onSelect: () => replace(index, { ...path, shifts: [] }),
            },
          ]
        : []),
    ]
    const where = group && group.id !== path.ids[at] ? `, in ${own}` : ''
    return { label: `Step ${label}`, heading: { title: label, detail: `step ${at + 1} of ${name}${where}` }, items }
  }

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
  const figure = () => exportProfiles && figureOf({ data: exportProfiles, colours, names, settings, shifts, chosen })

  const exportSvg = () => {
    const svg = figure()
    if (svg) saveProfileSvg(svg)
  }

  useEffect(() => {
    if (!figureAsked || stored === null) return
    const refusal = !drawn.length
      ? 'The open profile shows no pathway, so there is nothing to draw'
      : !request
        ? 'There are no energies to draw yet'
        : exportProfiles
          ? null
          : error // the profile could not be worked out
    if (!refusal && !exportProfiles) return // still being worked out
    // After this render: drawing the figure renders it off screen (figureOf).
    const timer = setTimeout(() => {
      setFigureAsked(null)
      const svg = refusal ? null : figure()
      if (!svg) return onFigure?.({ error: refusal || 'Could not draw the profile' })
      const name = `${fileName(`energy-profile ${openProfile?.name ?? ''}`.trim(), 'energy-profile')}.${figureAsked}`
      const made = figureAsked === 'svg' ? Promise.resolve(profileSvgBlob(svg)) : profilePngBlob(svg, style)
      made.then(
        (blob) => onFigure?.({ name, blob }),
        (err: unknown) => onFigure?.({ error: errorText(err) }),
      )
    }, 0)
    return () => clearTimeout(timer)
  })

  const exportPng = () => {
    const svg = figure()
    if (svg) saveProfilePng(svg, style, () => setError('Could not export the profile image'))
  }

  const exportCsv = () => {
    if (!request || !exportTable) return
    const key = resultKey
    api.energyTableCsv(request).then(
      (blob) => {
        if (currentResultKey.current === key) downloadBlob(blob, 'energy-table.csv')
      },
      (err: unknown) => {
        if (currentResultKey.current !== key) return
        setResult((previous) => previous?.key === key ? { ...previous, error: errorText(err) } : previous)
      },
    )
  }

  const shownError = shownResult?.error ?? error
  const errorLine = shownError && (
    <p role="alert" className="error small">
      {shownError}
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
              disabled: !exportProfiles,
              title: 'At the size and resolution of the profile style (Style…)',
            },
            { label: 'Profile as SVG', onSelect: exportSvg, disabled: !exportProfiles },
            { label: 'Table as CSV', onSelect: exportCsv, disabled: !exportTable, title: csvTitle(type) },
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
            {listed.map(({ path, at: index, colour, name, branched }) => (
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
                  <PathwayColour
                    name={name}
                    colour={swatchOf(index, colour, path)}
                    chosen={path.colour}
                    fallback={branched ? 'Branch colour' : 'Default colour'}
                    onChange={(next) => replace(index, { ...path, colour: next })}
                  />
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
                  <button
                    className="small"
                    onClick={() => remove(index, path, `Removed “${name}”.`)}
                    aria-label={`Remove pathway ${name}`}
                    title="Remove this pathway from the profile; Undo below the list puts it back (D221)"
                  >
                    ✕
                  </button>
                </div>
                {choiceRow('Start before', path.before, (choice) => extendBack(index, path, choice.node_id))}
                <p className="pathway-nodes">
                  {path.ids.map((id, i) => (
                    <span key={i}>
                      {i > 0 && ' → '}
                      {path.shifts.some((shift) => shift.at === i) && (
                        <span className="shift-mark" title="Moved later along the axis, with the steps after it (D194)">
                          {`+${path.shifts.find((shift) => shift.at === i)?.by} `}
                        </span>
                      )}
                      <button
                        className="link"
                        aria-haspopup="menu"
                        title="Select it on the canvas and open this step's menu: swap, branch off, start or end here, move"
                        onClick={(event) => {
                          onSelectNode(id)
                          openStep(event.currentTarget, index, i, path)
                        }}
                        onContextMenu={(event) => {
                          event.preventDefault()
                          openStep(event.currentTarget, index, i, path)
                        }}
                      >
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
                {moving?.index === index &&
                  moving.at < path.ids.length &&
                  (() => {
                    // D194: one step, and with it every step after it, moved along the axis.
                    // D228: the buttons come out from the step's menu and go with Done.
                    const at = moving.at
                    const by = path.shifts.find((shift) => shift.at === at)?.by ?? 0
                    const move = (delta: number) => replace(index, { ...path, shifts: shifted(path.shifts, at, delta) })
                    return (
                      <div className="pathway-move" role="group" aria-label={`Move ${name} along the axis`}>
                        <span className="muted small" title="This step and every step after it move; the steps before stay put">
                          Move {labels.get(path.ids[at])}
                          {by ? ` (+${by})` : ''} and the steps after it
                        </span>
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
                        <button className="small quiet" onClick={() => setMoving(null)} title="Put the Move buttons away">
                          Done
                        </button>
                      </div>
                    )
                  })()}
                {choiceRow('Continue with', path.choices, (choice) =>
                  extend(index, path, [...path.ids, choice.node_id]),
                )}
              </div>
            ))}
            {lost.map(({ path, at: index }) => {
              const left = path.ids.filter((id) => labels.has(id))
              const gone = path.ids.length - left.length
              const name = left.map((id) => labels.get(id)).join(' → ') || 'no node left'
              return (
                <div key={index} className="pathway lost-path" role="group" aria-label="Pathway that lost a node">
                  <div className="pathway-head">
                    <strong>Not drawn: {gone === 1 ? 'a node' : `${gone} nodes`} on it {gone === 1 ? 'was' : 'were'} deleted</strong>
                    <button
                      className="small"
                      onClick={() => remove(index, path, 'Removed the pathway that lost a node.')}
                      aria-label="Remove the pathway that lost a node"
                      title="Remove what is left of this pathway from the profile"
                    >
                      ✕
                    </button>
                  </div>
                  <p className="pathway-nodes muted">What is left: {name}</p>
                </div>
              )
            })}
            {notice && (
              <p className="pathway-notice small" role="status">
                {notice.text}
                {notice.undo && (
                  <>
                    {' '}
                    <button className="link" onClick={() => notice.undo && restore(notice.undo)}>
                      Undo
                    </button>
                  </>
                )}
              </p>
            )}
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
                  title="The node every pathway in this profile is relative to; the node cards keep their own (D205)"
                  onChange={(event) => setProfileRef(event.target.value || null)}
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
          {request && !shownResult && <p role="status">Loading energy results…</p>}
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
                chosen={chosen}
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
          preview={shownProfiles ? { data: shownProfiles, colours, names, shifts, chosen } : null}
          onSave={(profile_style) => api.saveSettings({ profile_style }).then((next) => onSettings?.(next))}
          onClose={() => setStyling(false)}
        />
      )}
      {stepMenu && (
        <ContextMenu x={stepMenu.x} y={stepMenu.y} content={stepContent} onClose={() => setStepMenu(null)} />
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
