import { ReactFlowProvider } from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  api,
  ApiError,
  energyTypeName,
  standardStateCorrection,
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
  type Settings,
  type StandardState,
  type SyncConflict,
  type SyncStatus,
} from './api'
import { NO_FILTERS, type Filters, type Selection, type ViewMode } from './canvasView'
import { BasisSetsDialog } from './components/BasisSets'
import { CanvasPane, type CanvasEnergy } from './components/Canvas'
import { ClaudePanel } from './components/ClaudePanel'
import { EnergyDrawer, type DrawerPath } from './components/EnergyDrawer'
import { download } from './util'
import { FilterMenu } from './components/FilterMenu'
import { FolderPicker } from './components/FolderPicker'
import { HistoryList } from './components/HistoryList'
import { UndoImportDialog } from './components/UndoImportDialog'
import { BatchImportDialog } from './components/BatchImportDialog'
import { ImportDialog } from './components/ImportDialog'
import { Modal } from './components/Modal'
import { NodeInspector } from './components/NodeInspector'
import { NoteEditor } from './components/NoteEditor'
import { Outline } from './components/Outline'
import { Overview } from './components/Overview'
import { AnalysesView } from './components/Analyses'
import { ClaudeRequestDialog } from './components/ClaudeRequestDialog'
import { useLive } from './live'
import type { HydrogenMode } from './chem'
import { recordNames } from './names'
import { HydrogenDisplay, StericColourSetting, type StericColours } from './display'
import { StericColourSelect } from './components/Sterics'
import {
  CloneDialog,
  ConflictDialog,
  LinkDialog,
  SyncButton,
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

function NumberSetting({
  label,
  value,
  onSave,
  allowZero = false,
}: {
  label: string
  value: number
  onSave: (value: number) => void
  allowZero?: boolean
}) {
  const [draft, setDraft] = useState(String(value))
  const parsed = Number(draft)
  const valid = draft.trim() !== '' && Number.isFinite(parsed) && (allowZero ? parsed >= 0 : parsed > 0)
  return (
    <label className="field">
      <span>{label}</span>
      <input
        aria-label={label}
        inputMode="decimal"
        value={draft}
        aria-invalid={!valid}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={() => valid && parsed !== value && onSave(parsed)}
      />
    </label>
  )
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** The display settings every 3D view, card and steric map reads. */
function Displays({
  hydrogens,
  sterics,
  children,
}: {
  hydrogens: HydrogenMode
  sterics: { colours: StericColours; setColours: (colours: StericColours) => void }
  children: ReactNode
}) {
  return (
    <HydrogenDisplay value={hydrogens}>
      <StericColourSetting value={sterics}>{children}</StericColourSetting>
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
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Git sync (D71)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [linking, setLinking] = useState(false)
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
  // D85: the pinned note being written: an existing one by id, or a new one on a node.
  const [noteEditing, setNoteEditing] = useState<{ noteId: string } | { nodeId: string } | null>(null)
  const [inspectorWidth, setInspectorWidth] = useState(520)
  // Energies (FR-EN-01): one level and one energy type for the whole view (EN-2, INV-5).
  const [energyOptions, setEnergyOptions] = useState<EnergyOptions | null>(null)
  const [energyLevel, setEnergyLevel] = useState<string | null>(null)
  const [energyType, setEnergyType] = useState<EnergyType>('G')
  const [energyView, setEnergyView] = useState<EnergyView | null>(null)
  const [edgeEnergies, setEdgeEnergies] = useState(true)
  const [referenceId, setReferenceId] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [claudeOpen, setClaudeOpen] = useState(false) // D92
  // D79: the read-only copy exports the drawer's pathways, or one per branch (A30).
  const [drawerPaths, setDrawerPaths] = useState<DrawerPath[]>([])
  const [sharing, setSharing] = useState<'ask' | 'saving' | null>(null)
  const [shareError, setShareError] = useState<string | null>(null)
  const resizing = useRef<{ x: number; width: number } | null>(null)

  useEffect(() => {
    api.currentInvestigation().then(setInvestigation, (err: unknown) => {
      setInvestigation(null)
      setError(`Backend not reachable: ${errorText(err)}`)
    })
    api.settings().then(setSettings, () => undefined)
  }, [])

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
  const claudeRequests = useLive(investigation?.folder ?? null, reload)

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
  // Only a view that matches the current selection is shown (INV-5).
  const shownView =
    energyView && levelKey && energyView.level === levelKey && energyView.type === shownType ? energyView : null

  useEffect(() => {
    if (!levelKey) return
    let current = true
    api.energyView(levelKey, shownType, referenceId).then(
      (found) => current && setEnergyView(found),
      (err: unknown) => current && setError(errorText(err)),
    )
    return () => {
      current = false
    }
  }, [levelKey, shownType, referenceId, energyOptions, refreshKey])

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
    setInvestigation(inv)
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
    setImportRequest((current) =>
      current && current.files.length > 1
        ? {
            ...current,
            files: current.files.slice(1),
            position: current.position && { x: current.position.x, y: current.position.y + 90 },
          }
        : null,
    )

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
    api.exportSnapshot(drawerPaths, referenceId).then(
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
          <h3>Quasi-harmonic free energy (G_qh)</h3>
          <NumberSetting
            label="Temperature (K)"
            value={settings.qh_temperature}
            onSave={(qh_temperature) =>
              api.saveSettings({ qh_temperature }).then((s) => {
                setSettings(s)
                reload()
              })
            }
          />
          <NumberSetting
            label="Cutoff (cm⁻¹)"
            value={settings.qh_cutoff}
            allowZero
            onSave={(qh_cutoff) =>
              api.saveSettings({ qh_cutoff }).then((s) => {
                setSettings(s)
                reload()
              })
            }
          />
          <p className="muted small">
            Frequencies below the cutoff are raised to it for the vibrational entropy only (Truhlar), as in the
            reference script. Every G_qh value is shown with these two numbers.
          </p>
          <h3>Standard state of G and G_qh</h3>
          <label className="field">
            <span>Standard state</span>
            <select
              aria-label="Standard state"
              value={settings.standard_state}
              onChange={(event) =>
                api.saveSettings({ standard_state: event.target.value as StandardState }).then((s) => {
                  setSettings(s)
                  reload()
                })
              }
            >
              <option value="1 atm">1 atm ideal gas (as in the output files)</option>
              <option value="1 M">1 M (1 mol/L)</option>
            </select>
          </label>
          <p className="muted small">
            1 M adds RT ln(V<sub>m</sub> / 1 L mol⁻¹) to the free energy of every node and free species, with the
            ideal-gas molar volume V<sub>m</sub> at the temperature above:{' '}
            {(standardStateCorrection(settings.qh_temperature) * settings.energy_factors[settings.energy_unit]).toFixed(
              settings.energy_decimals[settings.energy_unit] + 2,
            )}{' '}
            {settings.energy_unit} at {settings.qh_temperature} K. It changes relative free energies only where the
            number of molecules changes (species joining or leaving). E and H are not changed.
          </p>
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
          key={`${importRequest.targetId}-${importRequest.files.length}-${importRequest.files[0]?.name ?? ''}`}
          file={importRequest.files[0] ?? null}
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
            importRequest.targetId || importRequest.species || importRequest.files.length > 0
              ? undefined
              : (folder) => {
                  setImportRequest(null)
                  setBatchFolder(folder)
                }
          }
        />
      )}
      {batchFolder && investigation && (
        <BatchImportDialog
          folder={batchFolder}
          nodes={[...canvas.nodes, ...canvas.species]}
          linked={investigation.linked}
          onClose={() => setBatchFolder(null)}
          onImported={(result, message) => {
            setBatchFolder(null)
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
        </div>
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
        return api.createSelectivity({ name: `Selectivity ${n}`, level: levelKey, outcomes })
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
        onChanged={reload}
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
    <Displays hydrogens={settings?.hydrogens ?? 'all'} sterics={stericColours}>
      <div className="app">
        <header className="topbar">
          <strong className="brand">Chembook3D</strong>
          <span className="investigation" title={investigation.folder}>
            {investigation.name}
          </span>
          <nav className="tabs" aria-label="Views">
            <button aria-pressed={view === 'canvas'} onClick={() => setView('canvas')}>
              Canvas
            </button>
            <button aria-pressed={view === 'history'} onClick={() => setView('history')}>
              History
            </button>
            <button
              aria-pressed={view === 'analyses'}
              title="Selectivities: ΔΔG‡ and predicted ratios from competing transition states"
              onClick={() => setView('analyses')}
            >
              Analyses
            </button>
            <button
              aria-pressed={view === 'canvas' && selection === null && multi.length === 0}
              title="Where the investigation stands: branches, open items, recent changes, step notes"
              onClick={() => {
                choose(null)
                setView('canvas')
              }}
            >
              Overview
            </button>
          </nav>
          {view === 'canvas' && (
            <>
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
              <div className="energy-select" role="group" aria-label="Energy view">
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
                      {energyTypeName(t, energyOptions?.temperature, energyOptions?.cutoff, energyOptions?.standard_state)}
                    </option>
                  ))}
                </select>
                {energyOptions?.standard_state === '1 M' && (shownType === 'G' || shownType === 'G_qh') && (
                  <span
                    className="badge"
                    title="Free energies are at the 1 M standard state: each node and free species includes RT ln(V_m / 1 L mol⁻¹) (Settings)"
                  >
                    1 M
                  </span>
                )}
                <label className="check" title="Show ΔX on transitions (FR-CAN-03)">
                  <input
                    type="checkbox"
                    checked={edgeEnergies}
                    onChange={(event) => setEdgeEnergies(event.target.checked)}
                  />
                  <span>Energies on edges</span>
                </label>
              </div>
              <button aria-pressed={drawerOpen} onClick={() => setDrawerOpen(!drawerOpen)}>
                Profile and table
              </button>
            </>
          )}
          <span className="spacer" />
          <button
            aria-pressed={claudeOpen}
            onClick={() => setClaudeOpen(!claudeOpen)}
            title="Claude Code in a panel beside the notebook (D92)"
          >
            Claude
          </button>
          {investigation.linked ? (
            <SyncButton status={sync} busy={syncing} onSync={syncNow} />
          ) : (
            <button onClick={() => setLinking(true)}>Sync with GitHub…</button>
          )}
          <button onClick={() => setPicker('open')}>Open…</button>
          <button onClick={() => setPicker('create')}>New…</button>
          <button onClick={close}>Close</button>
          <button
            onClick={() => {
              setShareError(null)
              setSharing('ask')
            }}
            title="Save a read-only HTML file to send to others (D79)"
          >
            Share read-only copy…
          </button>
          <button onClick={() => setShowBases(true)} title="The saved custom basis sets, to inspect and download (D94)">
            Basis sets
          </button>
          <button onClick={() => setShowSettings(true)}>Settings</button>
        </header>
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
                levelLabel={levelOption?.label ?? ''}
                type={shownType}
                referenceId={referenceId}
                selectedId={selection && selection.kind !== 'edge' && selection.kind !== 'branch' ? selection.id : null}
                refreshKey={refreshKey}
                onReference={setReferenceId}
                onPathsChange={setDrawerPaths}
                onTurnover={newTurnover}
                onSelectNode={(id) => (canvas.groups.some((g) => g.id === id) ? selectGroup(id) : selectNode(id, true))}
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
          <ClaudePanel
            key={investigation.folder}
            open={claudeOpen}
            onClose={() => setClaudeOpen(false)}
            onAttention={() => setClaudeOpen(true)}
          />
        </div>
        {dialogs}
      </div>
    </Displays>
  )
}

export default App
