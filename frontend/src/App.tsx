import { ReactFlowProvider } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  api,
  ApiError,
  energyTypeName,
  SYNC_PROBLEMS,
  type Canvas,
  type EnergyOptions,
  type EnergyType,
  type EnergyView,
  type Group,
  type GroupLayout,
  type HistoryEntry,
  type Investigation,
  type Node,
  type NoteLayout,
  type InspectTarget,
  type ScanEditorTarget,
  type RemoteServer,
  type RemoteStatus,
  type Settings,
  type SyncConflict,
  type SyncStatus,
  type ViewState,
} from './api'
import { NO_FILTERS, type Filters, type Selection, type ViewMode } from './canvasView'
import { BasisSetsDialog } from './components/BasisSets'
import { CloudJobsDialog } from './components/CloudJobsDialog'
import { useScanPathImports } from './cloudJobs'
import { CanvasPane, type CanvasEnergy } from './components/Canvas'
import { EnergyDrawer, type DrawerPath, type DrawerView } from './components/EnergyDrawer'
import { download } from './util'
import { FilterMenu } from './components/FilterMenu'
import { FolderPicker } from './components/FolderPicker'
import { GModelBadge } from './components/GModelBadge'
import { HistoryList } from './components/HistoryList'
import { NumberSetting } from './components/NumberSetting'
import { ThermoSettings } from './components/ThermoSettings'
import { UndoImportDialog } from './components/UndoImportDialog'
import { BatchImportDialog } from './components/BatchImportDialog'
import { ImportDialog } from './components/ImportDialog'
import { InspectWindow } from './components/InspectWindow'
import { ScanEditor } from './components/ScanEditor'
import { OpenScanEditor } from './scanEditor'
import { IrcWindow } from './components/IrcWindow'
import { StructureEditor } from './components/StructureEditor'
import { RemoteFilesDialog } from './components/RemoteFilesDialog'
import { Modal } from './components/Modal'
import { NodeInspector } from './components/NodeInspector'
import { NoteEditor } from './components/NoteEditor'
import { Outline } from './components/Outline'
import { Overview } from './components/Overview'
import { AnalysesView } from './components/Analyses'
import { ClaudeRequestDialog } from './components/ClaudeRequestDialog'
import { SidePanel, type SideTab } from './components/SidePanel'
import { HPC, hpcLabel } from './hpc'
import { LaunchNotices, ShutDownButton, ShutDownDialog } from './components/ShutDown'
import { Menu } from './components/MoreMenu'
import { Icon } from './icons'
import { useLive } from './live'
import type { HydrogenMode } from './chem'
import { recordNames } from './names'
import { HydrogenDisplay, StericColourSetting, type StericColours } from './display'
import { EnergyNotes } from './components/EnergyNotes'
import { StericColourSelect } from './components/Sterics'
import {
  CloneDialog,
  ConflictDialog,
  LinkDialog,
  SyncChip,
  SyncState,
  UpgradeDialog,
} from './components/SyncDialogs'
import {
  BranchInspector,
  GroupInspector,
  GroupOrderDialog,
  SelectionInspector,
  TransitionInspector,
} from './components/PathwayInspectors'

type LockPrompt = { folder: string; host?: string; openedAt?: string }

/** One import dialog at a time; dropped files wait in a queue (WF-04: one or more files). */
type ImportRequest = {
  targetId: string | null
  files: File[]
  position: { x: number; y: number } | null
  /** Create a free species rather than a node (D69). */
  species?: boolean
  /** D122e: an output on an SSH server instead of a file here. */
  remote?: { server: string; path: string }
  /** D123c: the file read in the Inspect window, staged from its copy. */
  inspection?: { id: string; name: string }
  /** D137: a joined scan from the scan editor, shown on this edge only. */
  pathEdge?: string | null
}

/** An edge's end as the scan editor lists it: the node's label, or the group's. */
function endLabel(canvas: Canvas, id: string): string {
  const node = canvas.nodes.find((n) => n.id === id)
  if (node) return node.label || 'Untitled node'
  const group = canvas.groups.find((g) => g.id === id)
  return group ? group.label || 'Group' : '?'
}

type InspectorSelection = Selection | { kind: 'branch'; id: string }

const EMPTY_CANVAS: Canvas = { nodes: [], species: [], steps: [], branches: [], transitions: [], groups: [], notes: [] }

function readMode(): ViewMode {
  try {
    const stored = localStorage.getItem('chembook3d.mode')
    return stored === 'structure' || stored === 'energy' ? stored : 'compact'
  } catch {
    return 'compact'
  }
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** The display settings every 3D view, card and steric map reads, and what opens the scan
 * editor from inside a panel (D135). */
function Displays({
  hydrogens,
  sterics,
  scanEditor,
  children,
}: {
  hydrogens: HydrogenMode
  sterics: { colours: StericColours; setColours: (colours: StericColours) => void }
  scanEditor: (targets: ScanEditorTarget[]) => void
  children: ReactNode
}) {
  return (
    <HydrogenDisplay value={hydrogens}>
      <StericColourSetting value={sterics}>
        <OpenScanEditor.Provider value={scanEditor}>{children}</OpenScanEditor.Provider>
      </StericColourSetting>
    </HydrogenDisplay>
  )
}

function App() {
  const [investigation, setInvestigation] = useState<Investigation | null | undefined>(undefined)
  const [settings, setSettings] = useState<Settings | null>(null)
  // D84: the steric maps' colours, changed from the settings or beside any map.
  const stericColours = useMemo(
    () => ({
      colours: settings?.steric_colours ?? 'blue',
      setColours: (steric_colours: StericColours) => {
        api.saveSettings({ steric_colours }).then(setSettings, () => undefined)
      },
    }),
    [settings?.steric_colours],
  )
  const [picker, setPicker] = useState<'open' | 'create' | null>(null)
  const [lockPrompt, setLockPrompt] = useState<LockPrompt | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showBases, setShowBases] = useState(false) // D94
  const [showJobs, setShowJobs] = useState(false) // D115
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Git sync (D71)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [linking, setLinking] = useState(false)
  const [shuttingDown, setShuttingDown] = useState(false) // D180: asked from the investigation menu
  const [canvasTools, setCanvasTools] = useState<HTMLDivElement | null>(null) // D180: Fit and Export
  const [cloning, setCloning] = useState<{ url: string; name: string } | 'address' | null>(null)
  const [conflict, setConflict] = useState<SyncConflict | null>(null)
  const [upgrade, setUpgrade] = useState<{ folder: string } | null>(null)

  const [view, setView] = useState<'canvas' | 'history' | 'analyses'>('canvas')
  // D83, D86: the selectivity or turnover to show when the Analyses view opens
  const [openAnalysis, setOpenAnalysis] = useState<string | null>(null)
  const [canvas, setCanvas] = useState<Canvas>(EMPTY_CANVAS)
  const [selection, setSelection] = useState<InspectorSelection>(null)
  const [multi, setMulti] = useState<string[]>([])
  // One thing is shown in the inspector; choosing it ends any multi-selection.
  const choose = (next: InspectorSelection) => {
    setSelection(next)
    setMulti([])
  }
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null)
  const [mode, setMode] = useState<ViewMode>(readMode)
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [undoing, setUndoing] = useState<HistoryEntry | null>(null) // D102
  const [refreshKey, setRefreshKey] = useState(0)
  const [importRequest, setImportRequest] = useState<ImportRequest | null>(null)
  // D97: the folder whose outputs are being imported at once.
  const [batchFolder, setBatchFolder] = useState<string | null>(null)
  // D122e: "Import from here" on a server's tab, then the files chosen there.
  const [remoteFiles, setRemoteFiles] = useState<RemoteServer | null>(null)
  const [remoteBatch, setRemoteBatch] = useState<{ server: string; paths: string[] } | null>(null)
  // D123c: the files shown in the Inspect window. D129: several are one list, stepped through in
  // the window; a new list replaces it.
  const [inspecting, setInspecting] = useState<{ id: number; targets: InspectTarget[] } | null>(null)
  const [ircNode, setIrcNode] = useState<string | null>(null) // D131: the node whose IRC window is open
  const [editingNode, setEditingNode] = useState<string | null>(null) // D139: the Edit structure window's node
  const inspectCount = useRef(0)
  const inspect = useCallback((targets: InspectTarget[]) => {
    if (targets.length === 0) return
    inspectCount.current += 1
    setInspecting({ id: inspectCount.current, targets })
  }, [])
  // D135: the files the scan editor opened with; a new list opens a new editor.
  const [scanEditing, setScanEditing] = useState<{ id: number; targets: ScanEditorTarget[] } | null>(null)
  const openScanEditor = useCallback((targets: ScanEditorTarget[]) => {
    inspectCount.current += 1
    setScanEditing({ id: inspectCount.current, targets })
  }, [])
  // D85: the pinned note being written: an existing one by id, or a new one on a node.
  const [noteEditing, setNoteEditing] = useState<{ noteId: string } | { nodeId: string } | null>(null)
  const [inspectorWidth, setInspectorWidth] = useState(520)
  // Energies (FR-EN-01): one level and one energy type for the whole view (EN-2, INV-5).
  const [energyOptions, setEnergyOptions] = useState<EnergyOptions | null>(null)
  const [energyLevel, setEnergyLevel] = useState<string | null>(null)
  const [energyType, setEnergyType] = useState<EnergyType>('G')
  const [energyView, setEnergyView] = useState<EnergyView | null>(null)
  const [edgeEnergies, setEdgeEnergies] = useState(true)
  // D161: add each node's counterpoise BSSE at the level; off by default and never remembered.
  const [withBsse, setWithBsse] = useState(false)
  const [referenceId, setReferenceId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  // D92, D122: the side panel's tab (Claude or an SSH server), or null when it is hidden.
  const [sideTab, setSideTab] = useState<SideTab | null>(null)
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const loadRemote = useCallback(() => api.remoteStatus().then(setRemote, () => undefined), [])
  // A cd in a server's terminal: Send to and Import from here follow it without a reload (D122).
  const followCwd = useCallback(
    (id: string, cwd: string | null) =>
      setRemote((r) => r && { ...r, servers: r.servers.map((s) => (s.id === id ? { ...s, cwd } : s)) }),
    [],
  )
  useEffect(() => {
    void loadRemote()
  }, [loadRemote])
  // D79: the read-only copy exports the drawer's pathways, or one per branch (A30).
  const [drawerPaths, setDrawerPaths] = useState<DrawerPath[]>([])
  // D105: the drawer's tab and pathways, kept here so they outlast the drawer (History,
  // Analyses) and are remembered in the investigation; `drawerKey` starts it anew from them.
  const [drawerView, setDrawerView] = useState<DrawerView | null>(null)
  const [drawerKey, setDrawerKey] = useState(0)
  // D105: counts the investigations shown; the view is restored once for each, and saved only
  // after that, so the defaults shown while it loads never overwrite what was remembered.
  const [shown, setShown] = useState(0)
  const restored = useRef<number | null>(null)
  // What was saved last; null until the restored view has been seen, which is not saved again.
  // Opening an investigation must not change its database (D71: nothing new to sync).
  const lastSaved = useRef<string | null>(null)
  const [sharing, setSharing] = useState<'ask' | 'saving' | null>(null)
  const [shareError, setShareError] = useState<string | null>(null)
  const resizing = useRef<{ x: number; width: number } | null>(null)

  useEffect(() => {
    api.currentInvestigation().then(
      (inv) => {
        setInvestigation(inv)
        setShown((n) => n + 1)
      },
      (err: unknown) => {
        setInvestigation(null)
        setError(`Backend not reachable: ${errorText(err)}`)
      },
    )
    api.settings().then(setSettings, () => undefined)
  }, [])

  // D105: open on what the investigation showed when it was last used. Records deleted since
  // are left out by the backend; a level or type no longer offered falls back as usual.
  const shownFolder = investigation?.folder ?? null
  useEffect(() => {
    restored.current = null
    if (!shownFolder) return
    let current = true
    const done = (seen: string | null) => {
      if (!current) return
      restored.current = shown
      lastSaved.current = seen
    }
    api.viewState().then((saved) => {
      if (!current) return
      setEnergyLevel(saved.level)
      setEnergyType(saved.energy_type ?? 'G')
      setReferenceId(saved.reference_id)
      setEdgeEnergies(saved.edge_energies)
      setFilters(saved.filters)
      setExpanded(new Set(saved.expanded))
      setDrawerOpen(saved.drawer.open)
      setDrawerView({ tab: saved.drawer.tab, paths: saved.drawer.paths })
      setDrawerKey((k) => k + 1)
      done(null)
    }, () => done(''))
    return () => {
      current = false
    }
  }, [shownFolder, shown])

  // D105: every change to these is remembered at once, one save after the other.
  const saving = useRef<Promise<unknown>>(Promise.resolve())
  useEffect(() => {
    if (restored.current !== shown || !drawerView) return
    const state: ViewState = {
      level: energyLevel,
      energy_type: energyType,
      reference_id: referenceId,
      edge_energies: edgeEnergies,
      filters,
      expanded: [...expanded],
      drawer: { open: drawerOpen, ...drawerView },
    }
    const text = JSON.stringify(state)
    if (lastSaved.current === null || lastSaved.current === text) {
      lastSaved.current = text
      return
    }
    lastSaved.current = text
    saving.current = saving.current.then(() => api.saveViewState(state)).catch(() => undefined)
  }, [shown, energyLevel, energyType, referenceId, edgeEnergies, filters, expanded, drawerOpen, drawerView])

  // Answers to earlier fetches can arrive after later ones (slow runners, a large import); only
  // the latest is shown, or a stale canvas without a just-created node would drop its inspector.
  const fetched = useRef(0)
  const fetchRecords = useCallback(() => {
    if (!investigation) return
    const n = ++fetched.current
    const latest =
      <T,>(set: (value: T) => void) =>
      (value: T) => {
        if (n === fetched.current) set(value)
      }
    const failed = (err: unknown) => n === fetched.current && setError(errorText(err))
    api.canvas().then(latest(setCanvas), failed)
    api.history().then(latest(setHistory), failed)
    api.energyOptions().then(latest(setEnergyOptions), failed)
  }, [investigation])

  useEffect(fetchRecords, [fetchRecords])

  const reload = () => {
    fetchRecords()
    setRefreshKey((k) => k + 1)
  }

  // D91: changes Claude (or another tab) makes show at once; Claude's deletes wait for an answer.
  // D159: the investigation's thermochemistry is in the settings, so they are read again too.
  const claudeRequests = useLive(investigation?.folder ?? null, () => {
    reload()
    api.settings().then(setSettings, () => undefined)
  })

  // D115: a scan path that came back from its cloud session becomes a node by itself.
  const pathImported = (label: string, _nodeId: string, onEdge: boolean) => {
    reload()
    setNotice(
      onEdge
        ? `Scan path imported as “${label}”, shown as a chip on its edge (it can be undone in History).`
        : `Scan path imported as “${label}”, between its two ends (it can be undone in History).`,
    )
  }
  useScanPathImports(investigation?.folder ?? null, investigation?.linked ?? false, pathImported, (job, message) =>
    setNotice(`The scan path “${job.name}” could not be imported: ${message} (Cloud jobs)`),
  )

  // The chosen level, or else the first with G (D32: free energy preferred), or else the first.
  const levelOption =
    energyOptions?.levels.find((l) => l.key === energyLevel) ??
    energyOptions?.levels.find((l) => l.types.includes('G')) ??
    energyOptions?.levels[0] ??
    null
  const shownType: EnergyType = levelOption
    ? levelOption.types.includes(energyType)
      ? energyType
      : levelOption.types.includes('G')
        ? 'G'
        : levelOption.types[0]
    : energyType
  const levelKey = levelOption?.key ?? null
  const bsse = withBsse && !!levelOption?.bsse
  const levelLabel = levelOption ? levelOption.label + (bsse ? ' with BSSE' : '') : ''
  // Only a view that matches the current selection is shown (INV-5).
  const shownView =
    energyView &&
    levelKey &&
    energyView.level === levelKey &&
    energyView.type === shownType &&
    !!energyView.bsse === bsse
      ? energyView
      : null

  useEffect(() => {
    if (!levelKey) return
    let current = true
    api.energyView(levelKey, shownType, referenceId, bsse).then(
      (found) => current && setEnergyView(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [levelKey, shownType, referenceId, bsse, energyOptions, refreshKey])

  // D91: what this tab shows, for Claude's get_selection ("this node"). The tab used last wins.
  const groupIds = useMemo(() => new Set(canvas.groups.map((g) => g.id)), [canvas.groups])
  useEffect(() => {
    if (!investigation) return
    const many = multi.length >= 2
    const report = {
      nodes: many ? multi.filter((id) => !groupIds.has(id)) : selection?.kind === 'node' ? [selection.id] : [],
      groups: many ? multi.filter((id) => groupIds.has(id)) : selection?.kind === 'group' ? [selection.id] : [],
      transitions: !many && selection?.kind === 'edge' ? [selection.id] : [],
      branch_id: !many && selection?.kind === 'branch' ? selection.id : null,
      view,
      level: levelKey,
      energy_type: levelKey ? shownType : null,
      reference_id: referenceId,
    }
    const send = () => void api.reportSelection(report).catch(() => undefined)
    const timer = setTimeout(send, 150)
    window.addEventListener('focus', send)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('focus', send)
    }
  }, [investigation, selection, multi, groupIds, view, levelKey, shownType, referenceId])

  // Stable between renders, so the canvas only rebuilds its nodes when energies change.
  const canvasEnergy: CanvasEnergy = useMemo(
    () => ({ view: shownView, type: shownType, showEdges: edgeEnergies, referenceId, settings }),
    [shownView, shownType, edgeEnergies, referenceId, settings],
  )

  /** fresh: the investigation was closed in between (to resolve a sync conflict), so it is
   * shown anew even if it is the one this render still has. */
  const opened = (inv: Investigation, fresh = false) => {
    if (inv.sync) setSync(inv.sync)
    else if (!inv.linked) setSync(null)
    // Opened, but the pull did not get through (FR-SYNC-04): say why.
    const syncProblem = inv.sync && SYNC_PROBLEMS.includes(inv.sync.state) ? `Sync: ${inv.sync.message}` : null
    // Opening the investigation already shown keeps the canvas and selection: resetting them when
    // the answer arrives would drop whatever was clicked in the meantime.
    if (!fresh && investigation && investigation.folder === inv.folder) {
      setError(null)
      setNotice(syncProblem)
      setView('canvas')
      return
    }
    setCanvas(EMPTY_CANVAS)
    setHistory([])
    setEnergyOptions(null)
    setEnergyLevel(null)
    setReferenceId(null)
    choose(null)
    setFilters(NO_FILTERS)
    setExpanded(new Set())
    setDrawerOpen(false)
    setDrawerView(null)
    setDrawerKey((k) => k + 1)
    setInvestigation(inv)
    setShown((n) => n + 1)
    setError(null)
    setNotice(syncProblem)
    setView('canvas')
    api.settings().then(setSettings, () => undefined)
  }

  const openFailed = (folder: string) => (err: unknown) => {
    if (err instanceof ApiError && err.lock) {
      setLockPrompt({ folder, host: err.lock.host, openedAt: err.lock.opened_at })
    } else if (err instanceof ApiError && err.syncConflict) {
      setConflict(err.syncConflict)
    } else if (err instanceof ApiError && err.needsUpgrade) {
      setUpgrade({ folder })
    } else {
      setError(errorText(err))
    }
  }

  const openFolder = (folder: string, force = false, upgradeConfirmed = false) => {
    setPicker(null)
    setLockPrompt(null)
    setUpgrade(null)
    api.openInvestigation(folder, force, upgradeConfirmed).then(opened, openFailed(folder))
  }

  const cloneInto = (url: string, folder: string) => {
    setCloning(null)
    setNotice(null)
    setError(null)
    api.cloneInvestigation(url, folder).then(opened, openFailed(folder))
  }

  const syncNow = () => {
    if (!investigation) return
    setSyncing(true)
    api.syncNow().then(
      (status) => {
        setSyncing(false)
        setSync(status)
        if (status.state === 'diverged') setConflict({ ...status, folder: investigation.folder })
        else if (SYNC_PROBLEMS.includes(status.state)) setError(`Sync: ${status.message}`)
        else setNotice(`Sync: ${status.message}`)
      },
      (err: unknown) => {
        setSyncing(false)
        setError(errorText(err))
      },
    )
  }

  const keepCopy = (keep: 'this' | 'github') => {
    if (!conflict) return
    const folder = conflict.folder
    // The backend closes the investigation to resolve; it is opened again afterwards.
    if (investigation && investigation.folder === folder) setInvestigation(null)
    api.resolveSync(folder, keep).then(
      () => {
        setConflict(null)
        api.openInvestigation(folder).then((inv) => opened(inv, true), openFailed(folder))
      },
      (err: unknown) => {
        setConflict(null)
        setError(errorText(err))
      },
    )
  }

  const createAt = (folder: string, name: string) => {
    setPicker(null)
    api.createInvestigation(folder, name).then(opened, (err: unknown) => setError(errorText(err)))
  }

  const close = () => {
    const folder = investigation?.folder
    api.closeInvestigation().then((status) => {
      setInvestigation(null)
      setSync(null)
      // Closing pushes a linked investigation (FR-SYNC-05).
      if (status && folder && status.state === 'diverged') setConflict({ ...status, folder })
      else if (status && SYNC_PROBLEMS.includes(status.state)) setError(`Sync: ${status.message}`)
      api.settings().then(setSettings, () => undefined)
    })
  }

  const selectNode = (id: string, centre = false) => {
    setNotice(null)
    choose({ kind: 'node', id })
    setView('canvas')
    // A free species is not on the canvas, so there is nothing to centre (D69).
    const onCanvas = canvas.nodes.find((n) => n.id === id)
    if (centre && onCanvas) setFocus({ id: onCanvas.group_id ?? id, n: Date.now() })
  }

  const addSpecies = () =>
    api.createNode({ kind: 'species', label: `Species ${canvas.species.length + 1}` }).then(
      (node) => {
        reload()
        choose({ kind: 'node', id: node.id })
        setView('canvas')
      },
      (err: unknown) => setError(errorText(err)),
    )

  const addNode = (position?: { x: number; y: number }) => {
    const spot = position ?? {
      x: canvas.nodes.reduce((max, n) => Math.max(max, n.pos_x), -220) + 220,
      y: 0,
    }
    api.createNode({ pos_x: spot.x, pos_y: spot.y }).then(
      (node) => {
        reload()
        choose({ kind: 'node', id: node.id })
        setView('canvas')
        if (!position) setFocus({ id: node.id, n: Date.now() })
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const nextImport = () =>
    setImportRequest((current) => {
      // The Inspect window's copy is no longer needed once its import is done or cancelled.
      if (current?.inspection) void api.closeInspection(current.inspection.id)
      return current && current.files.length > 1
        ? {
            ...current,
            files: current.files.slice(1),
            position: current.position && { x: current.position.x, y: current.position.y + 90 },
          }
        : null
    })

  const changed = (node: Node, message?: string) => {
    setNotice(message ?? null)
    choose({ kind: 'node', id: node.id })
    reload()
  }

  const savePositions = (positions: Record<string, { x: number; y: number }>) => {
    // Shown at once; saved in the background. Positions are layout, not history.
    setCanvas((current) => ({
      ...current,
      nodes: current.nodes.map((n) => (positions[n.id] ? { ...n, pos_x: positions[n.id].x, pos_y: positions[n.id].y } : n)),
      groups: current.groups.map((g) => (positions[g.id] ? { ...g, pos_x: positions[g.id].x, pos_y: positions[g.id].y } : g)),
    }))
    api.savePositions(positions).catch((err: unknown) => setError(errorText(err)))
  }

  const setGroupLayout = useCallback((id: string, layout: GroupLayout) => {
    // Shown at once; saved in the background, like positions (A21).
    setCanvas((current) => ({ ...current, groups: current.groups.map((g) => (g.id === id ? { ...g, layout } : g)) }))
    api.updateGroup(id, { layout }).catch((err: unknown) => setError(errorText(err)))
  }, [])

  // D89: the group whose member order list is open
  const [ordering, setOrdering] = useState<string | null>(null)
  const orderGroup = useCallback((id: string) => setOrdering(id), [])

  const openNote = useCallback((noteId: string) => setNoteEditing({ noteId }), [])
  const layoutNote = useCallback(
    (noteId: string, layout: Partial<NoteLayout>) =>
      void api.updateNote(noteId, layout).then(fetchRecords, (err: unknown) => setError(errorText(err))),
    [fetchRecords],
  )

  const toggleGroup = useCallback((id: string) => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const chooseMode = (next: ViewMode) => {
    setMode(next)
    try {
      localStorage.setItem('chembook3d.mode', next)
    } catch {
      // per-browser convenience only
    }
  }

  const saveSnapshot = () => {
    setSharing('saving')
    setShareError(null)
    // D128: the copy opens on what is shown here.
    const view = {
      mode,
      level: energyLevel,
      energy_type: energyType,
      edge_energies: edgeEnergies,
      filters,
      expanded: [...expanded],
      drawer: { open: drawerOpen, tab: drawerView?.tab ?? 'profile' },
    }
    api.exportSnapshot(drawerPaths, referenceId, view).then(
      ({ blob, name }) => {
        const url = URL.createObjectURL(blob)
        download(url, name)
        setTimeout(() => URL.revokeObjectURL(url), 1000)
        setSharing(null)
        setNotice(`Saved “${name}”. It opens in a browser, offline, and cannot be edited.`)
      },
      (err: unknown) => {
        setSharing('ask')
        setShareError(errorText(err))
      },
    )
  }

  const labels = useMemo(
    () => new Map([...canvas.nodes, ...canvas.species].map((n) => [n.id, n.label])),
    [canvas.nodes, canvas.species],
  )
  const names = useMemo(() => recordNames(canvas), [canvas])

  const canvasSelection: Selection = selection && selection.kind !== 'branch' ? selection : null
  const multiNodes = multi.map((id) => canvas.nodes.find((n) => n.id === id)).filter((n): n is Node => !!n)
  const multiGroups = multi.map((id) => canvas.groups.find((g) => g.id === id)).filter((g): g is Group => !!g)

  const editedNote = noteEditing && 'noteId' in noteEditing ? canvas.notes.find((n) => n.id === noteEditing.noteId) : null
  const notedNodeId = editedNote ? editedNote.node_id : noteEditing && 'nodeId' in noteEditing ? noteEditing.nodeId : null
  const notedNode = notedNodeId ? canvas.nodes.find((n) => n.id === notedNodeId) : undefined

  const dialogs = (
    <StericColourSetting value={stericColours}>
      {claudeRequests.length > 0 && (
        <ClaudeRequestDialog key={claudeRequests[0].id} request={claudeRequests[0]} onError={setError} />
      )}
      {notedNode && (
        <NoteEditor
          note={editedNote ?? null}
          nodeId={notedNode.id}
          nodeLabel={notedNode.label || 'Untitled node'}
          onClose={() => setNoteEditing(null)}
          onSaved={() => {
            setNoteEditing(null)
            reload()
          }}
        />
      )}
      {picker && (
        <FolderPicker
          mode={picker}
          onCancel={() => setPicker(null)}
          onChoose={(folder, name) => (picker === 'open' ? openFolder(folder) : createAt(folder, name))}
        />
      )}
      {cloning === 'address' && (
        <CloneDialog onCancel={() => setCloning(null)} onNext={(url, name) => setCloning({ url, name })} />
      )}
      {cloning && cloning !== 'address' && (
        <FolderPicker
          mode="create"
          title="Where to put the investigation"
          chooseLabel="Copy from GitHub"
          initialName={cloning.name}
          onCancel={() => setCloning(null)}
          onChoose={(folder) => cloneInto(cloning.url, folder)}
        />
      )}
      {linking && (
        <LinkDialog
          onCancel={() => setLinking(false)}
          onLinked={(status) => {
            setLinking(false)
            setSync(status)
            if (investigation) setInvestigation({ ...investigation, linked: true, sync: status })
            setNotice(`Sync: ${status.message}`)
          }}
        />
      )}
      {conflict && <ConflictDialog conflict={conflict} onCancel={() => setConflict(null)} onKeep={keepCopy} />}
      {upgrade && (
        <UpgradeDialog
          folder={upgrade.folder}
          onCancel={() => setUpgrade(null)}
          onUpgrade={() => openFolder(upgrade.folder, false, true)}
        />
      )}
      {ordering && canvas.groups.some((g) => g.id === ordering) && (
        <GroupOrderDialog
          group={canvas.groups.find((g) => g.id === ordering)!}
          nodes={canvas.nodes}
          onClose={() => setOrdering(null)}
          onChanged={fetchRecords}
        />
      )}
      {lockPrompt && (
        <Modal
          title="Investigation may be open elsewhere"
          onClose={() => setLockPrompt(null)}
          actions={
            <>
              <button onClick={() => setLockPrompt(null)}>Cancel</button>
              <button className="danger" onClick={() => openFolder(lockPrompt.folder, true)}>
                Open anyway
              </button>
            </>
          }
        >
          <p>
            <code>{lockPrompt.folder}</code> was opened on <strong>{lockPrompt.host ?? 'another computer'}</strong>
            {lockPrompt.openedAt && <> at {new Date(lockPrompt.openedAt + 'Z').toLocaleString()}</>} and has
            not been closed.
          </p>
          <p>
            If it is really open there, changes from both places can damage the database, especially in a
            synced folder. Open it anyway only if you are sure it is closed (for example after a crash).
          </p>
        </Modal>
      )}
      {showJobs && investigation && (
        <CloudJobsDialog
          linked={investigation.linked}
          onClose={() => setShowJobs(false)}
          onImported={pathImported}
          onSelectNode={(id) => {
            setView('canvas')
            selectNode(id, true)
          }}
        />
      )}
      {showBases && (
        <BasisSetsDialog
          onClose={() => setShowBases(false)}
          onSelectNode={(id) => {
            setView('canvas')
            selectNode(id, true)
          }}
        />
      )}
      {showSettings && settings && (
        <Modal
          title="Settings"
          onClose={() => setShowSettings(false)}
          actions={<button onClick={() => setShowSettings(false)}>Done</button>}
        >
          <label className="field">
            <span>Energy unit</span>
            <select
              aria-label="Energy unit"
              value={settings.energy_unit}
              onChange={(event) => api.saveSettings({ energy_unit: event.target.value }).then(setSettings)}
            >
              {settings.energy_units.map((unit) => (
                <option key={unit}>{unit}</option>
              ))}
            </select>
          </label>
          <p className="muted">Relative energies use this unit; absolute energies are shown in hartree.</p>
          <h3>Geometry tolerances (RMSD after alignment, Å)</h3>
          <NumberSetting
            label="Same geometry"
            value={settings.geometry_tolerance}
            onSave={(geometry_tolerance) => api.saveSettings({ geometry_tolerance }).then(setSettings)}
          />
          <p className="muted small">A job step's results attach to a node only within this RMSD (ID-7).</p>
          <NumberSetting
            label="Possible duplicate"
            value={settings.duplicate_tolerance}
            onSave={(duplicate_tolerance) => api.saveSettings({ duplicate_tolerance }).then(setSettings)}
          />
          <p className="muted small">An import this close to another node is flagged as a possible duplicate (ID-8).</p>
          <ThermoSettings
            key={`${settings.thermo_scope}:${investigation?.folder ?? ''}`}
            settings={settings}
            investigationName={investigation?.name ?? null}
            onSaved={(s) => {
              setSettings(s)
              if (s.thermo_scope === 'investigation') reload()
            }}
          />
          <h3>CREST ensembles</h3>
          <NumberSetting
            label="Conformers kept on import"
            value={settings.crest_count}
            onSave={(count) => api.saveSettings({ crest_count: Math.max(1, Math.round(count)) }).then(setSettings)}
          />
          <p className="muted small">The lowest this many conformers are ticked in the import preview (D34).</p>
          <h3>Structures</h3>
          <label className="field">
            <span>Hydrogens</span>
            <select
              aria-label="Hydrogens"
              value={settings.hydrogens}
              onChange={(event) =>
                api.saveSettings({ hydrogens: event.target.value as Settings['hydrogens'] }).then(setSettings)
              }
            >
              <option value="all">Show all</option>
              <option value="polar">Hide those bonded to carbon</option>
              <option value="none">Hide all</option>
            </select>
          </label>
          <p className="muted small">
            Applies to the 3D views and the structure cards. “Hide those bonded to carbon” keeps hydrides, O–H and N–H,
            and C–H hydrogens also close to a metal (agostic).
          </p>
          <h3>Steric maps</h3>
          <StericColourSelect />
          <p className="muted small">
            Colours from low (far below the centre) to high (crowding it). Difference maps stay blue–grey–red.
          </p>
        </Modal>
      )}
      {sharing && (
        <Modal
          title="Share a read-only copy"
          onClose={() => setSharing(null)}
          actions={
            <>
              <button onClick={() => setSharing(null)}>Cancel</button>
              <button className="primary" disabled={sharing === 'saving'} onClick={saveSnapshot}>
                {sharing === 'saving' ? 'Saving…' : 'Save file'}
              </button>
            </>
          }
        >
          <p>
            Saves this investigation as one HTML file that opens in a current browser, offline, with nothing to install.
            Send it to a supervisor or co-author: they can look around but cannot change anything.
          </p>
          <p>
            It holds the canvas, notes, calculations and their results, the 3D structures and the energies at every level.
            Energy profiles and the table show{' '}
            {drawerPaths.length
              ? `the ${drawerPaths.length === 1 ? 'pathway' : `${drawerPaths.length} pathways`} in “Profile and table”.`
              : 'one pathway per branch. To choose others, add them under “Profile and table” first.'}
          </p>
          <p className="muted small">
            Left out: the folders and file paths on this computer, the output files themselves, and the change history.
            Notes are included as written.
          </p>
          {shareError && <p role="alert">{shareError}</p>}
        </Modal>
      )}
      {importRequest && investigation && (
        <ImportDialog
          key={`${importRequest.targetId}-${importRequest.files.length}-${importRequest.files[0]?.name ?? ''}-${importRequest.remote?.path ?? ''}-${importRequest.inspection?.id ?? ''}-${importRequest.pathEdge ?? ''}`}
          file={importRequest.files[0] ?? null}
          remote={importRequest.remote ?? null}
          inspection={importRequest.inspection ?? null}
          pathEdge={importRequest.pathEdge ?? null}
          target={[...canvas.nodes, ...canvas.species].find((n) => n.id === importRequest.targetId) ?? null}
          nodes={[...canvas.nodes, ...canvas.species]}
          asSpecies={importRequest.species ?? false}
          linked={investigation.linked}
          energyUnit={settings?.energy_unit ?? 'kcal/mol'}
          energyFactor={settings ? (settings.energy_factors[settings.energy_unit] ?? 1) : 627.5094740631}
          queued={Math.max(0, importRequest.files.length - 1)}
          position={importRequest.position}
          onClose={nextImport}
          onImported={(imported, message) => {
            setNotice(message)
            choose(imported)
            if (imported.kind === 'group') setFocus({ id: imported.id, n: Date.now() })
            setView('canvas')
            reload()
            nextImport()
          }}
          onImportFolder={
            importRequest.targetId ||
            importRequest.species ||
            importRequest.files.length > 0 ||
            importRequest.remote ||
            importRequest.inspection
              ? undefined
              : (folder) => {
                  setImportRequest(null)
                  setBatchFolder(folder)
                }
          }
          onInspect={(paths) => {
            setImportRequest(null)
            inspect(paths.map((path) => ({ path })))
          }}
          onScanEditor={(paths) => {
            setImportRequest(null)
            openScanEditor(paths.map((path) => ({ path })))
          }}
        />
      )}
      {remoteFiles && investigation && (
        <RemoteFilesDialog
          server={remoteFiles}
          onClose={() => setRemoteFiles(null)}
          onImport={({ server, paths, batch }) => {
            setRemoteFiles(null)
            if (batch) setRemoteBatch({ server, paths })
            else setImportRequest({ targetId: null, files: [], position: null, remote: { server, path: paths[0] } })
          }}
          onInspect={(targets) => {
            setRemoteFiles(null)
            inspect(targets)
          }}
          onScanEditor={(targets) => {
            setRemoteFiles(null)
            openScanEditor(targets)
          }}
        />
      )}
      {ircNode && investigation && canvas.nodes.some((n) => n.id === ircNode) && (
        <IrcWindow
          key={ircNode}
          node={canvas.nodes.find((n) => n.id === ircNode)!}
          nodes={[...canvas.nodes, ...canvas.species]}
          settings={settings}
          linked={investigation.linked}
          onClose={() => setIrcNode(null)}
          onChanged={(message) => {
            setNotice(message)
            reload()
          }}
          onShowNode={(id) => {
            setIrcNode(null)
            selectNode(id, true)
          }}
          servers={remote?.servers.filter((s) => s.connected) ?? []}
        />
      )}
      {editingNode && investigation && [...canvas.nodes, ...canvas.species].some((n) => n.id === editingNode) && (
        <StructureEditor
          key={editingNode}
          node={[...canvas.nodes, ...canvas.species].find((n) => n.id === editingNode)!}
          nodes={[...canvas.nodes, ...canvas.species]}
          onClose={() => setEditingNode(null)}
          onSaved={(saved, derived) => {
            const original = [...canvas.nodes, ...canvas.species].find((n) => n.id === editingNode)
            setEditingNode(null)
            changed(
              saved,
              derived ? `Saved as a new node derived from “${original?.label || 'Untitled node'}”.` : 'Structure saved.',
            )
          }}
        />
      )}
      {scanEditing && (
        <ScanEditor
          key={scanEditing.id}
          targets={scanEditing.targets}
          settings={settings}
          edges={
            investigation
              ? canvas.transitions.map((t) => ({ id: t.id, name: `${endLabel(canvas, t.source_id)} → ${endLabel(canvas, t.target_id)}` }))
              : null
          }
          servers={remote?.servers.filter((s) => s.connected) ?? []}
          hidden={Boolean(importRequest)}
          onImport={(file, edge) => setImportRequest({ targetId: null, files: [file], position: null, pathEdge: edge })}
          onClose={() => setScanEditing(null)}
        />
      )}
      {inspecting && (
        <InspectWindow
          key={inspecting.id}
          targets={inspecting.targets}
          settings={settings}
          canImport={Boolean(investigation)}
          onClose={() => setInspecting(null)}
          onScanEditor={(target) => {
            setInspecting(null)
            openScanEditor([target])
          }}
          onImport={(found) => {
            setInspecting(null)
            setImportRequest({
              targetId: null,
              files: [],
              position: null,
              inspection: { id: found.id, name: found.name },
            })
          }}
        />
      )}
      {(batchFolder || remoteBatch) && investigation && (
        <BatchImportDialog
          folder={batchFolder ?? ''}
          remote={remoteBatch}
          nodes={[...canvas.nodes, ...canvas.species]}
          linked={investigation.linked}
          onClose={() => {
            setBatchFolder(null)
            setRemoteBatch(null)
          }}
          onImported={(result, message) => {
            setBatchFolder(null)
            setRemoteBatch(null)
            setNotice(message)
            const first = result.files.find((f) => f.how === 'new' || f.how === 'derived' || f.how === 'group')
            if (first?.group_id) choose({ kind: 'group', id: first.group_id })
            else if (first?.node_id) choose({ kind: 'node', id: first.node_id })
            if (first) setFocus({ id: (first.group_id ?? first.node_id)!, n: Date.now() })
            setView('canvas')
            reload()
          }}
        />
      )}
    </StericColourSetting>
  )

  if (investigation === undefined) return <p className="loading">Connecting to backend…</p>

  if (investigation === null) {
    return (
      <main className="start">
        <h1>Chembook3D</h1>
        <p className="muted">A notebook for computational chemistry mechanism investigations.</p>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="buttons">
          <button className="primary" onClick={() => setPicker('create')}>
            New investigation…
          </button>
          <button onClick={() => setPicker('open')}>Open investigation…</button>
          <button onClick={() => setCloning('address')}>Open from GitHub…</button>
          <button
            onClick={() => openScanEditor([])}
            title="Put several scans together into one path, without an investigation (D135)"
          >
            Scan editor
          </button>
          <span className="spacer" />
          <ShutDownButton />
        </div>
        <LaunchNotices />
        {settings && settings.recent.length > 0 && (
          <section>
            <h2>Recently opened</h2>
            <ul className="recent" aria-label="Recently opened">
              {settings.recent.map((folder) => (
                <li key={folder}>
                  <button className="link" onClick={() => openFolder(folder)}>
                    {folder}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {dialogs}
      </main>
    )
  }

  const selectedNode =
    selection?.kind === 'node'
      ? (canvas.nodes.find((n) => n.id === selection.id) ?? canvas.species.find((n) => n.id === selection.id))
      : undefined
  const selectedGroup = selection?.kind === 'group' ? canvas.groups.find((g) => g.id === selection.id) : undefined
  const selectedEdge = selection?.kind === 'edge' ? canvas.transitions.find((t) => t.id === selection.id) : undefined
  const selectedBranch = selection?.kind === 'branch' ? canvas.branches.find((b) => b.id === selection.id) : undefined
  const selectGroup = (id: string) => {
    choose({ kind: 'group', id })
    setFocus({ id, n: Date.now() })
  }
  const toggleNode = (id: string) =>
    setMulti((current) => {
      const base = current.length
        ? current
        : selection?.kind === 'node' || selection?.kind === 'group'
          ? [selection.id]
          : []
      return base.includes(id) ? base.filter((x) => x !== id) : [...base, id]
    })
  const selectBranch = (id: string) => choose({ kind: 'branch', id })
  const selectEdge = (id: string) => choose({ kind: 'edge', id })
  const cleared = () => {
    choose(null)
    reload()
  }

  // D83: one outcome per selected TS or group, named after it; the user renames them later.
  const newSelectivity = (nodes: Node[], groups: Group[]) => {
    const used = new Set<string>()
    const outcomes = [...groups, ...nodes].map((item) => {
      const base = ('kind' in item ? item.label || 'Untitled node' : item.label || 'Group').trim()
      let name = base
      for (let n = 2; used.has(name); n++) name = `${base} (${n})`
      used.add(name)
      return { name, members: [item.id], experimental: null }
    })
    api
      .selectivities()
      .then((existing) => {
        const taken = new Set(existing.map((s) => s.name))
        let n = existing.length + 1
        while (taken.has(`Selectivity ${n}`)) n++
        return api.createSelectivity({
          name: `Selectivity ${n}`,
          level: levelKey,
          // D103: balanced from the energy view's reference, as the node cards are
          reference_id: referenceId,
          outcomes,
        })
      })
      .then(
        (created) => {
          setOpenAnalysis(created.id)
          setView('analyses')
          reload()
        },
        (err: unknown) => setError(errorText(err)),
      )
  }

  // D171: a conformer equilibrium of the selected conformers, TSs and groups.
  const newEquilibrium = (nodes: Node[], groups: Group[]) => {
    const members = [...groups.map((g) => g.id), ...nodes.map((n) => n.id)].map((id) => ({ id, count: 1 }))
    api
      .equilibria()
      .then((existing) => {
        const taken = new Set(existing.map((e) => e.name))
        let n = existing.length + 1
        while (taken.has(`Conformer equilibrium ${n}`)) n++
        return api.createEquilibrium({ name: `Conformer equilibrium ${n}`, level: levelKey, members })
      })
      .then(
        (created) => {
          setOpenAnalysis(created.id)
          setView('analyses')
          reload()
        },
        (err: unknown) => setError(errorText(err)),
      )
  }

  // D86: a turnover of a closed pathway from the drawer, named after the pathway.
  const newTurnover = (path: string[], name: string) => {
    api
      .turnovers()
      .then((existing) => {
        const taken = new Set(existing.map((t) => t.name))
        let unique = name
        for (let n = 2; taken.has(unique); n++) unique = `${name} (${n})`
        return api.createTurnover({ name: unique, path, level: levelKey })
      })
      .then(
        (created) => {
          setOpenAnalysis(created.id)
          setView('analyses')
          reload()
        },
        (err: unknown) => setError(errorText(err)),
      )
  }

  let inspector
  if (multiNodes.length + multiGroups.length >= 2) {
    inspector = (
      <SelectionInspector
        nodes={multiNodes}
        groups={multiGroups}
        canvas={canvas}
        onChanged={reload}
        onGroupCreated={(group) => {
          choose({ kind: 'group', id: group.id })
          reload()
        }}
        onSelectivity={() => newSelectivity(multiNodes, multiGroups)}
        onEquilibrium={() => newEquilibrium(multiNodes, multiGroups)}
      />
    )
  } else if (selectedNode) {
    inspector = (
      <NodeInspector
        key={selectedNode.id}
        node={selectedNode}
        canvas={canvas}
        refreshKey={refreshKey}
        onChanged={changed}
        onSelect={(id) => selectNode(id, true)}
        onSelectGroup={selectGroup}
        onSelectBranch={selectBranch}
        onSelectTransition={selectEdge}
        onDeleted={cleared}
        onImport={() => setImportRequest({ targetId: selectedNode.id, files: [], position: null })}
        onDropFiles={(files) => setImportRequest({ targetId: selectedNode.id, files, position: null })}
        onRefresh={fetchRecords}
        isReference={referenceId === selectedNode.id}
        onUseAsReference={() => setReferenceId(selectedNode.id)}
        onEditNote={(noteId) => setNoteEditing(noteId ? { noteId } : { nodeId: selectedNode.id })}
        settings={settings}
        servers={remote?.enabled ? remote.servers.filter((s) => s.connected) : []}
        onIrc={() => setIrcNode(selectedNode.id)}
        onEditStructure={() => setEditingNode(selectedNode.id)}
        energy={canvasEnergy}
        levelLabel={levelLabel}
      />
    )
  } else if (selectedGroup) {
    inspector = (
      <GroupInspector
        key={selectedGroup.id}
        group={selectedGroup}
        canvas={canvas}
        onChanged={reload}
        onRemoved={cleared}
        onSelectNode={(id) => selectNode(id, true)}
        onSelectBranch={selectBranch}
        energy={{ view: shownView, typeName: shownType, settings }}
      />
    )
  } else if (selectedEdge) {
    inspector = (
      <TransitionInspector
        key={selectedEdge.id}
        transition={selectedEdge}
        canvas={canvas}
        settings={settings}
        refreshKey={refreshKey}
        onChanged={reload}
        onNotice={(text) => {
          reload()
          if (text) setNotice(text)
        }}
        onDeleted={cleared}
        onSelectNode={(id) => selectNode(id, true)}
        onSelectGroup={selectGroup}
      />
    )
  } else if (selectedBranch) {
    inspector = (
      <BranchInspector
        key={selectedBranch.id}
        branch={selectedBranch}
        canvas={canvas}
        onChanged={reload}
        onDeleted={cleared}
        onSelectNode={(id) => selectNode(id, true)}
        onSelectBranch={selectBranch}
        onArranged={() => {
          reload()
          const first = canvas.nodes.find((n) => n.branch_id === selectedBranch.id)
          if (first) setFocus({ id: first.id, n: Date.now() })
        }}
      />
    )
  } else {
    // WF-10: with nothing selected, the side panel shows the resume overview.
    inspector = (
      <Overview
        canvas={canvas}
        refreshKey={refreshKey}
        labels={labels}
        names={names}
        onSelectNode={(id) => (canvas.groups.some((g) => g.id === id) ? selectGroup(id) : selectNode(id, true))}
        onSelectTransition={(id) => {
          selectEdge(id)
          const edge = canvas.transitions.find((t) => t.id === id)
          const source = edge && canvas.nodes.find((n) => n.id === edge.source_id)
          if (edge) setFocus({ id: source?.group_id ?? edge.source_id, n: Date.now() })
        }}
        onSelectBranch={selectBranch}
      />
    )
  }

  return (
    <Displays hydrogens={settings?.hydrogens ?? 'all'} sterics={stericColours} scanEditor={openScanEditor}>
      <div className="app">
        {/* D180 (UI2): one app bar. The investigation's commands are in the menu on its name;
            the view tabs; then tools, the side-panel toggles, the sync chip and Settings. */}
        <header className="topbar appbar">
          <strong className="brand">
            <Icon name="hexagon" />
            Chembook3D
          </strong>
          <Menu
            label="Investigation"
            heading={investigation.name}
            className="investigation-menu"
            buttonClass="investigation"
            buttonLabel={`${investigation.name}, investigation menu`}
            title={investigation.folder}
            items={[
              { label: 'New investigation…', icon: 'plus', onSelect: () => setPicker('create') },
              { label: 'Open investigation…', icon: 'folder', onSelect: () => setPicker('open') },
              { label: 'Close investigation', icon: 'x', onSelect: close },
              investigation.linked
                ? {
                    label: 'Sync with GitHub',
                    icon: 'branch',
                    detail: <SyncState status={sync} busy={syncing} />,
                    disabled: syncing,
                    onSelect: syncNow,
                    separated: true,
                  }
                : { label: 'Sync with GitHub…', icon: 'branch', onSelect: () => setLinking(true), separated: true },
              {
                label: 'Share read-only copy…',
                icon: 'share',
                title: 'Save a read-only HTML file to send to others (D79)',
                onSelect: () => {
                  setShareError(null)
                  setSharing('ask')
                },
              },
              {
                label: 'Basis sets',
                icon: 'book',
                title: 'The saved custom basis sets, to inspect and download (D94)',
                onSelect: () => setShowBases(true),
              },
              {
                label: 'Shut down Chembook3D',
                icon: 'power',
                danger: true,
                separated: true,
                title: 'Close the investigation and stop Chembook3D',
                onSelect: () => setShuttingDown(true),
              },
            ]}
          >
            <span className="investigation-name">{investigation.name}</span>
            <Icon name="chevron" />
          </Menu>
          <span className="vsep" />
          <nav className="tabs" aria-label="Views">
            <button aria-pressed={view === 'canvas'} onClick={() => setView('canvas')}>
              Canvas
            </button>
            <button aria-pressed={view === 'history'} onClick={() => setView('history')}>
              History
            </button>
            <button
              aria-pressed={view === 'analyses'}
              title="Selectivities, turnovers and conformer equilibria"
              onClick={() => setView('analyses')}
            >
              Analyses
            </button>
          </nav>
          <span className="spacer" />
          <button
            className="quiet"
            onClick={() => openScanEditor([])}
            title="Put several scans together into one path, without importing them (D135)"
          >
            <Icon name="wave" />
            <span className="tb-label">Scan editor</span>
          </button>
          <button
            className="quiet"
            onClick={() => setShowJobs(true)}
            title="Calculations handed to Claude Code cloud sessions (D93, D115)"
          >
            <Icon name="cloud" />
            <span className="tb-label">Cloud jobs</span>
          </button>
          <span className="vsep" />
          <button
            className="quiet"
            aria-pressed={sideTab === 'claude'}
            onClick={() => setSideTab(sideTab === 'claude' ? null : 'claude')}
            title="Claude Code in a panel beside the notebook (D92)"
          >
            <Icon name="spark" />
            <span className="tb-label">Claude</span>
          </button>
          {remote?.enabled && (
            <button
              className="quiet"
              aria-pressed={sideTab !== null && sideTab !== 'claude'}
              onClick={() => {
                const first = remote.servers.find((s) => s.connected)
                setSideTab(sideTab !== null && sideTab !== 'claude' ? null : (first?.id ?? HPC))
              }}
              title="Log in to a cluster such as Saga: a terminal there, with files to and from its current directory (D122, D127)"
            >
              <Icon name="terminal" />
              <span className="tb-label">{hpcLabel(remote.servers)}</span>
            </button>
          )}
          <span className="vsep" />
          <SyncChip
            linked={investigation.linked}
            status={sync}
            busy={syncing}
            onSync={syncNow}
            onLink={() => setLinking(true)}
          />
          <button className="quiet icon-only" aria-label="Settings" title="Settings" onClick={() => setShowSettings(true)}>
            <Icon name="sliders" />
          </button>
        </header>
        {view === 'canvas' && (
          // D180: the canvas's own controls, in one row under the app bar.
          <div className="canvasbar">
            <div className="segmented" role="group" aria-label="Node view">
              <button aria-pressed={mode === 'compact'} onClick={() => chooseMode('compact')}>
                Compact
              </button>
              <button aria-pressed={mode === 'energy'} onClick={() => chooseMode('energy')}>
                Energy
              </button>
              <button aria-pressed={mode === 'structure'} onClick={() => chooseMode('structure')}>
                Structure
              </button>
            </div>
            <FilterMenu canvas={canvas} filters={filters} onChange={setFilters} />
            <span className="vsep" />
            <div className="energy-select" role="group" aria-label="Energy view">
              <span className="lbl">Energies</span>
              <select
                aria-label="Level of theory"
                value={levelKey ?? ''}
                disabled={!levelOption}
                onChange={(event) => setEnergyLevel(event.target.value)}
              >
                {!levelOption && <option value="">No energies yet</option>}
                {energyOptions?.levels.map((l) => (
                  <option key={l.key} value={l.key}>
                    {l.label}
                  </option>
                ))}
              </select>
              <select
                aria-label="Energy type"
                value={shownType}
                disabled={!levelOption}
                onChange={(event) => setEnergyType(event.target.value as EnergyType)}
              >
                {(levelOption?.types ?? []).map((t) => (
                  <option key={t} value={t}>
                    {energyTypeName(
                      t,
                      energyOptions?.temperature,
                      energyOptions?.cutoff,
                      energyOptions?.standard_state,
                      levelOption?.job_temperatures,
                    )}
                  </option>
                ))}
              </select>
              {shownType === 'G' && <GModelBadge models={levelOption?.g_models} />}
              {energyOptions?.standard_state === '1 M' && (shownType === 'G' || shownType === 'G_qh') && (
                <span
                  className="badge"
                  title="Free energies are at the 1 M standard state: each node and free species includes RT ln(V_m / 1 L mol⁻¹) (Settings)"
                >
                  1 M
                </span>
              )}
              {levelOption?.bsse && (
                <label
                  className="check"
                  title="Add the BSSE of each node's counterpoise job at this level (the counterpoise correction, D161). Nodes without one are unchanged."
                >
                  <input type="checkbox" checked={withBsse} onChange={(event) => setWithBsse(event.target.checked)} />
                  <span>With BSSE</span>
                </label>
              )}
              {/* D160: W-TEMP and W-STDSTATE of the values shown */}
              <EnergyNotes notes={shownView?.notes} badges />
              <label className="check" title="Show ΔX on the arrows between nodes (FR-CAN-03)">
                <input
                  type="checkbox"
                  checked={edgeEnergies}
                  onChange={(event) => setEdgeEnergies(event.target.checked)}
                />
                <span>Show on arrows</span>
              </label>
            </div>
            <span className="spacer" />
            {/* Fit and Export: drawn here by the canvas (CanvasPane's tools) */}
            <div className="canvasbar-tools" ref={setCanvasTools} />
            <span className="vsep" />
            <button className="quiet" aria-pressed={drawerOpen} onClick={() => setDrawerOpen(!drawerOpen)}>
              <Icon name="bottompanel" />
              <span className="tb-label">Profile and table</span>
            </button>
          </div>
        )}
        {shuttingDown && <ShutDownDialog onClose={() => setShuttingDown(false)} />}
        <LaunchNotices />
        {(error || notice) && (
          <div className={error ? 'banner error' : 'banner'} role={error ? 'alert' : 'status'}>
            {error ?? notice}
            <button className="link" onClick={() => (error ? setError(null) : setNotice(null))}>
              Dismiss
            </button>
          </div>
        )}
        <div className="app-body">
        {view === 'canvas' ? (
          <div className="work-column">
            <div className="workspace">
              <Outline
                canvas={canvas}
                selectedNodeId={selection?.kind === 'node' ? selection.id : null}
                selectedBranchId={selection?.kind === 'branch' ? selection.id : null}
                onSelectNode={(id) => selectNode(id, true)}
                onToggleNode={toggleNode}
                selectedNodeIds={multi}
                onSelectBranch={selectBranch}
                onAdd={() => addNode()}
                onImport={() => setImportRequest({ targetId: null, files: [], position: null })}
                onAddSpecies={addSpecies}
                onImportSpecies={() => setImportRequest({ targetId: null, files: [], position: null, species: true })}
                onChanged={(branchId) => {
                  reload()
                  if (branchId) selectBranch(branchId)
                }}
              />
              <ReactFlowProvider>
                <CanvasPane
                  data={canvas}
                  mode={mode}
                  energy={canvasEnergy}
                  filters={filters}
                  selection={canvasSelection}
                  multi={multi}
                  focus={focus}
                  expanded={expanded}
                  onToggleGroup={toggleGroup}
                  onGroupLayout={setGroupLayout}
                  onGroupOrder={orderGroup}
                  onSelect={(next) => {
                    setNotice(null)
                    choose(next)
                  }}
                  onMultiSelect={setMulti}
                  onToggleNode={toggleNode}
                  onConnect={(source, target, sides) =>
                    api.createTransition(source, target, sides).then(reload, (err: unknown) => setError(errorText(err)))
                  }
                  onMoveEnds={(ids, sides) =>
                    Promise.all(ids.map((id) => api.updateTransition(id, sides))).then(reload, (err: unknown) =>
                      setError(errorText(err)),
                    )
                  }
                  onAddNode={addNode}
                  onDropFiles={(files, targetId, position) => setImportRequest({ targetId, files, position })}
                  onPositions={savePositions}
                  onError={setError}
                  onOpenNote={openNote}
                  onLayoutNote={layoutNote}
                  tools={canvasTools}
                />
              </ReactFlowProvider>
              <div
                className="splitter"
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize inspector"
                onPointerDown={(event) => {
                  resizing.current = { x: event.clientX, width: inspectorWidth }
                  event.currentTarget.setPointerCapture(event.pointerId)
                }}
                onPointerMove={(event) => {
                  if (!resizing.current) return
                  const width = resizing.current.width - (event.clientX - resizing.current.x)
                  setInspectorWidth(Math.min(1100, Math.max(340, width)))
                }}
                onPointerUp={() => (resizing.current = null)}
              />
              <main className="side" style={{ width: inspectorWidth }}>
                {inspector}
              </main>
            </div>
            <div hidden={!drawerOpen}>
              <EnergyDrawer
                canvas={canvas}
                settings={settings}
                level={levelKey}
                levelLabel={levelLabel}
                bsse={bsse}
                type={shownType}
                referenceId={referenceId}
                selectedId={selection && selection.kind !== 'edge' && selection.kind !== 'branch' ? selection.id : null}
                refreshKey={refreshKey}
                onReference={setReferenceId}
                onPathsChange={setDrawerPaths}
                key={drawerKey}
                saved={drawerView ?? undefined}
                onViewChange={setDrawerView}
                onTurnover={newTurnover}
                onSelectNode={(id) => (canvas.groups.some((g) => g.id === id) ? selectGroup(id) : selectNode(id, true))}
                onSettings={setSettings}
              />
            </div>
          </div>
        ) : view === 'analyses' ? (
          <AnalysesView
            key={openAnalysis ?? ''}
            canvas={canvas}
            settings={settings}
            energyOptions={energyOptions}
            defaultLevel={levelKey}
            refreshKey={refreshKey}
            openId={openAnalysis}
            onSelectNode={(id) => selectNode(id, true)}
            onChanged={reload}
          />
        ) : (
          <main className="main history-view">
            <h2>Investigation history</h2>
            <HistoryList
              entries={history}
              labels={labels}
              names={names}
              onSelect={(id) => selectNode(id, true)}
              onUndo={setUndoing}
            />
            {undoing && (
              <UndoImportDialog
                entry={undoing}
                onClose={() => setUndoing(null)}
                onDone={() => {
                  setUndoing(null)
                  reload()
                }}
              />
            )}
          </main>
        )}
          <SidePanel
            key={investigation.folder}
            tab={sideTab}
            onTab={setSideTab}
            remote={remote}
            onRemoteChanged={loadRemote}
            onImportFrom={setRemoteFiles}
            onInspect={inspect}
            onScanEditor={openScanEditor}
            onCwd={followCwd}
          />
        </div>
        {dialogs}
      </div>
    </Displays>
  )
}

export default App
