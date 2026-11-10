# Chembook3D code map

Where each feature lives: files, records, routes, MCP tools and the decisions behind them. One section per feature; list them with `grep -n "^## " docs/code-map.md` and read only the sections a task touches. When a change adds, renames or moves something a section names, update that section in the same pull request.

## Packages and shared helpers

- Layout of `src/chembook3d/` and `frontend/`: CLAUDE.md; processes and the import pipeline: `docs/architecture.md`.
- `api/` holds the HTTP routes, one module per feature (about 30). `api/routes.py` holds investigations, imports, nodes, settings, custom bases and sync.
- Frontend helpers, used instead of local copies:
  - `src/util.ts` `errorText`: a caught error as shown text; `{ blockers: true }` shows an ApiError's `blockers` instead. `download` (a URL) and `downloadBlob` (a Blob).
  - `src/names.ts` `nodeName`: a label, else "Untitled node".
  - `src/api.ts` `request` for JSON and `requestBlob` for a file fetched as a blob.
  - `src/api.ts` `displayUnit(settings)`: the display unit and its hartree factor (P17), kcal/mol before the settings load. Use it (or `energyFactor`, `convertEnergy`) rather than reading `energy_factors` directly: an unknown unit throws instead of showing hartree under another unit's label.

## Look of the interface (D174, D185)

- Every colour in `frontend/src/index.css` is a token on `:root` named for its use. Code that paints outside CSS reads one with `colourToken` in `util.ts`. Add or reuse a token, never a colour literal.
- Buttons and fields follow one system there: `--ctl-h`, primary / normal / `quiet` / `danger`, the `--focus` ring.
- `src/icons.tsx` holds the inline SVG icons (`<Icon name=… />`, Lucide shapes).
- Dark theme (D185): `theme.ts` (`setTheme`, `useTheme`; the app setting `theme`, mirrored in localStorage as `THEME_KEY`). The read-only copy and exported figures stay light.

## Investigation folder, records and migrations

- `src/chembook3d/investigation.py`: the investigation folder: database, `files/`, lock file, migrations with backup, clean-up of copies left by an interrupted import.
- One-off repairs of records an earlier version got wrong: `services/repairs.py`, run once per investigation when it is opened and remembered in `InvestigationInfo.repairs`.
- `investigation.py` refuses a database from a newer app (`InvestigationTooNew`) and, on request, asks before upgrading (`NeedsUpgrade`).
- Records in `models.py`, Alembic revisions in `migrations/`, rules in `services/`.
- Who owns the open folder (lock, `_OwnedSession`): see "Notebook identity and ownership".
- A new, empty database gets its schema from the models in one commit and is stamped at the latest revision (`_create_schema`); only an existing one runs the migrations (`_migrate`, after its backup). `tests/test_migrations.py` checks that both give the same schema, so a migration that adds a column with a server default needs the same `server_default` on the model.
- `_migrate` runs every pending revision and the foreign key check in one transaction (an explicit `BEGIN`, since Python's sqlite3 module would commit each DDL statement on its own), so a failed upgrade leaves the database exactly as its backup. `tests/test_migrations.py` seeds a row in every table at every revision and upgrades it, checks that a failed upgrade changes no byte, and walks every `downgrade()` back to the base and up again. A downgrade drops a foreign key by name only when the model names it too (a database created from the models has the others unnamed); dropping the column drops its key.
- In `api/routes.py`, create, open, clone and close each take a number when they start (`_begin_switch`). One that finishes after a later one started was overtaken: it closes its own investigation again and answers 409 `superseded` (`_set_open`, `tests/test_overtaken_switch.py`).
- `CHEMBOOK3D_SLOW_REQUESTS` (seconds; CI sets 2) makes `app.SlowRequests` print every request that took longer, long polls left out.

## Notebook identity and ownership (F02)

### One app owns a folder

- `acquire_ownership` retains an OS lock on the persistent `.chembook3d.owner` sidecar before any migration, cleanup or repair. `InvestigationOwnership.operation` serializes sync and close within the app. The JSON `.chembook3d.lock` marker identifies its owner.
- Where the filesystem cannot lock at all (an error other than "held"), only the JSON marker applies, so "Open anyway" still works.
- Dead same-host markers are reclaimed. Force may override an unheld marker from another host, but never a held OS lock (`can_force: false`). Only the matching token removes its marker.
- Sessions from an investigation use `_OwnedSession`: a thread-independent admission counter that drains checked-out sessions before disposal or database replacement and rejects new sessions once closing starts. A closed SQLAlchemy session and an old investigation's session factory cannot reconnect; reopening acquires a new ownership token. This also permits FastAPI dependency teardown on a different worker thread.
- Git sync: `sync.py`'s `pull`, `push`, `link` and `resolve` services retain investigation ownership for the whole operation; replacing a database requires a closed database under that lease. Routes in `api/routes.py` retain the same ownership across pull→open, close→push and close→resolve. The persistent owner sidecar is ignored in new links and locally excluded before syncing older linked folders.
- `app.py` maps an investigation/session lease refusal during close to a controlled HTTP 409.

### Each request names its tab's notebook

- `frontend/src/api.ts`'s `setInvestigationIdentity` binds JSON/blob requests to the displayed folder with the URI-encoded `X-Chembook-Investigation` header (empty means no notebook) and drops responses from earlier identity generations.
- `api/identity.py` `check_investigation_identity` binds a request to its tab's investigation: it checks the header against the captured active investigation and refuses a mismatch with 409. `api/live.py` checks before dispatch, and `api/routes.py` checks again at investigation/settings/close capture.
- Missing headers retain legacy/MCP compatibility. Discovery, folder browsing and explicit open/create/clone/targeted sync-resolution requests are exempt. The computer-wide `/remote` status read is also unscoped so it can load before the notebook identity is established.
- Close retains its captured request target and can return its sync outcome after live reports no open notebook.
- `GET /api/live` also accepts the displayed folder as `expected_investigation` (empty for the start page). Identity differences wake the poll independently of the change counter, including a same-tab close whose push is still pending. Legacy polls detect identity changes during their wait too.
- For browser downloads, custom-basis copy and note images, `api.ts`'s `investigationUrl` puts the rendered folder in `expected_investigation`. The backend accepts this query fallback when the header is absent (headers take precedence, each value is decoded once). Legacy clients with neither remain supported.

### The page follows it

- `frontend/src/live.ts` polls from the start page too and reconciles `live.investigation` before records reload. `App.tsx` uses `opened`/`closed` for both explicit and live changes.
- `App.tsx`'s `resetInvestigation` invalidates pending record/view responses and queued view saves, clears notebook-specific UI, and then starts the same restore flow for explicit opening and live switches. Reopening the displayed folder keeps its selection and view. Each queued view save checks its captured identity generation before sending; the HTTP identity check also refuses an A save delayed until B is open.
- `EnergyDrawer.tsx` stores profiles, table and computation errors together with the exact `ProfileRequest` and record refresh key (INV-5). Only a result for the same request is drawn, and only the matching refresh can be exported. A new request shows a loading state. A refresh of the same request (a record changed) keeps the last result drawn, with exports held until it arrives. Failures cannot expose earlier numbers, and a successful fetch clears an earlier drawer error. A delayed CSV download checks its initiating result key again before downloading. A CSV failure preserves the matching displayed scientific result.
- The drawer's serial profile writes use `api.ts`'s `captureInvestigation` guard: closing the drawer within the same investigation still finishes saving, while a queued write from an earlier investigation never dispatches using the new identity.

### Tests

- `tests/test_ownership.py` and `ownership_worker.py` exercise contention, crash recovery and sync lifecycle with real subprocesses. `tests/test_ownership.py` also checks actual local Git reset and backup preservation while a second process tries to open.
- `tests/test_live.py`: direct ASGI identity regressions that need no event loop, and the registered 409 exception handler through ASGI without a socket pair.
- `energy-request.spec.ts` delays/refuses all five scientific choices (type, level, reference, pathways, BSSE) and reverses profile/table/CSV completion.
- `investigation-identity.spec.ts` covers two tabs opening/switching/closing, an initially idle start page, stale responses/view writes, reload and a delayed close outcome.
- `frontend/tests/api-identity.test.mjs` exercises the real API client with deferred fetch bodies and no installed frontend dependencies.
- The other `frontend/tests/*.test.mjs` test the frontend's pure modules (`chem.ts`, `tidy.ts`, `labelLayout.ts`, `profileAxis.ts`, `snapshot/data.ts`, `util.ts`, the energy helpers in `api.ts`). Node loads the `.ts` files itself; `tests/resolve-ts.mjs` only adds the `.ts` extension the app's imports leave out. A module Node cannot load (React, CSS, `.tsx`) is not imported: its pure part is moved into a `.ts` module, as `profileAxis.ts` was. `npm run test:coverage` (what CI runs) adds Node's coverage of `src/`.

## Output parsers and importing a file (D61, D64)

- `src/chembook3d/parsers/`: output parsers, one `ParsedStep` per job step (`common.py`), all our own code, not cclib.
  - `gaussian.py`, `orca.py`, `xtb.py`; `crest.py` reads a conformer ensemble.
  - `cml.py` reads ioChem-BD's CML of a Gaussian output as `gaussian.py` reads the log (D186): one step per `cc:job`, route through `gaussian.parse_route`, basis via `gaussian._element_basis`; only the first and final structures, no correlated totals. Fixtures in `tests/fixtures/iochem/`, CC BY 4.0.
- `services/imports.py` detects which one a file is, stages it, plans which steps attach to which node (preview) and commits. A CREST ensemble imports as a group (`_plan_ensemble`, `_commit_ensemble`).
- Copies of imported files go to `files/<source-file-id>/<name>`; the stored path uses `/`.
- `services/levels.py`: levels of theory and the named custom basis sets and dispersions.
- `services/basis_sets.py` reads a saved basis set back into shells and ECP terms and writes it as a Gaussian `.gbs` file (D94; routes `/api/custom-bases`, dialog `components/BasisSets.tsx`).
- `services/warnings.py` computes W-* warnings; `units.py` holds the energy conversion factors.

### Geometry levels (D100, D204)

- `levels.optimization_at`: the optimization on a node that ended on a geometry. `levels.node_geometry_optimization`: the one the node's geometry came from.
- An optimization starting where the node's optimization at another level ended continues it (`imports.continued_optimization`, PRE-OPT): the node takes its result, no derived node.
- A single point's geometry level is the optimization ending on its geometry. D204: `levels.geometry_sources`; on a node without optimizations, its frequency jobs, W-FREQ-NONSTAT marking one whose check failed.

### Dispersion IOps (D203)

- `gaussian.dispersion_iops(iops, dispersion)` keeps IOp(3/174–179) only with a dispersion model (EmpiricalDispersion or IOp(3/124)).
- Repair `dispersion_without_model` moves older levels named that way to the level without dispersion.

### Held steps (D157)

- The parsers fill `ParsedStep.constraints`: Gaussian scans, ModRedundant S/F, frozen atoms; ORCA `%geom Scan`/`Constraints`; xTB `$constrain`/`$scan`/`$fix`; never an xTB scan path or joined scan.
- Kept as `Calculation.constraints` (migration 0022, repair `constrained_steps`).
- Such an import suggests no role and warns W-CONSTRAINED. `levels.optimizations`/`is_geometry_source` leave it out of every D100 rule.

## Batch import of a folder (D97, A44)

- `services/batch_import.py` scans a folder (`scan`, staged in `Staging.batches`).
- `run` matches each output: final geometry, a planned node's guess by the first geometry, file name against labels with suffixes taken off and a one-for-one `_` fallback, else a new node in a grid right of the canvas. It imports the files in order through `imports.plan`/`imports.commit`, matching each against the state the files before it left.
- The preview is the same run with `dry_run=True` (no copies, no settings) in a session that is always rolled back. Import runs it in the request's transaction with one `batch_import` history entry.
- Routes `/api/batch-imports` in `api/batch_imports.py` (not an MCP tool). `components/BatchImportDialog.tsx`, opened from the import dialog's file browser ("Import this folder…").
- A file that names no basis set (ChkBasis from another job's checkpoint, `imports.basis_not_named`) takes the batch's `missing_basis` or its row's `basis` (D104, `ImportOptions.missing_basis`). The calculation's `parsed_level` keeps the empty basis plus `basis_given`.

## Undoing an import (D102, A49)

- `services/import_undo.py` reads back from the history what one imported file wrote. `imports.commit` writes it in a fixed order: the node created or the target's changed fields, a derived node, then each calculation with its incomplete tag, an ensemble's group last.
- `_import_entries` walks out from the calculations' entries by these rules, plus a later D100 repair onto the import's optimization.
- It plans the undo (what is removed, put back, kept, and what blocks it) and applies it as new history. A batch is undone last file first (its entry names each file's `source_file_id`).
- Routes `GET`/`POST /api/history/{entry_id}/undo` in `api/import_undo.py`. The preview runs in a rolled-back session; the stored copies are removed after the commit.
- `HistoryOut.undo` says which entries can be undone; `components/UndoImportDialog.tsx`; the MCP confirm action `undo_import` (one file only).

## Download file names

- Download file names built from the user's text (node, basis set, investigation) all go through `api/downloads.py` (`file_name`, `attachment`) and its mirror `fileName` in `frontend/src/util.ts`.
- Only <>:"/\|?* and control characters become `_`, so primes, spaces and Greek letters stay.
- The header carries an ASCII `filename` and the real name in `filename*`.
- `tests/fixtures/file-names.json` holds shared examples (text and the expected name), read by both `tests/test_downloads.py` and `frontend/tests/util.test.mjs`, so the two copies of the rule cannot drift apart. Add a case there, not to one side only.

## Atom matching (D113, A59)

- `services/atom_matching.py` matches the end's atoms to the start's numbering: bond graphs by the 3D view's rule, unique-surroundings anchors, growth bond by bond, Hungarian pairing by position for the rest, several starting orientations, handedness of centres; scipy.
- `POST /api/atom-match` in `api/pathway.py` (MCP read tool `match_atoms`), nothing stored.
- `components/AtomMatchDialog.tsx` is the side-by-side review (`AtomMatchReview`, hand-fixed pairs), opened by the selection inspector's "Match atoms…". `Viewer3D`'s `marks` draws its numbers and dashed bond changes.

## Pathway structure (D53, D66, D76, D88, D89, D192)

Routes for all of these are in `api/pathway.py`.

- `services/steps.py`: reaction steps.
- `services/branches.py`: branches, lineage, split.
- `services/transitions.py`: edges.
  - "Direct" means neither end is a TS, a group counting as one when its representative is (D53, D111).
  - Each end is drawn on a side of its box (D76), which the canvas connects in React Flow's loose mode.
  - D192: a new transition given no status starts done when both its ends are done, else planned (`default_status`); after that its status is the user's alone.
- `services/groups.py`: reconnect, add members, take one out and keep it (D88), dissolve, delete with contents. Members keep their branch (D66). A group also stores its member layout, and its members' order in `Node.group_position`, which every layout follows (D89).
- `services/layout.py`: arrange a branch by step.
- `services/group_populations.py`: a group's members' Boltzmann population % (D197): `GroupNode.show_populations`, migration 0030, the energy view's `populations`, `frontend/src/populations.ts` for the cards and the group's panel.

## Sub-investigations (D193, A128)

### Records and rules

- `services/sub_investigations.py` holds the rules for `SubInvestigation` (migration 0029: label, notes, `parent_id` for nesting, `pos_x`/`pos_y` on its parent's canvas, `face_id` a node inside shown as a picture) and `Node.canvas_id`/`GroupNode.canvas_id` (None: the main canvas).
- A group's members always share its canvas, so `groups._join` moves a joining node there (Q60).
- The box is never an edge end and has no energy (Q59). Transitions are unchanged; one crossing canvases is drawn to the box.
- `create`: the selection must be on one canvas; `with_groups` lets a member stand for its group. `move`: onto a non-empty canvas right of its content (`GAP`). `dissolve`: centred on the box. `delete`: with contents, innermost first. `_check_faces` clears a face that left. `summaries` counts what each holds at any depth.

### Routes and MCP tools

- Routes `/api/sub-investigations` in `api/sub_investigations.py`. `GET /api/canvas` carries `sub_investigations` and each node's and group's `canvas_id`.
- New nodes take `canvas_id` (`NodeIn`, `ImportOptions`, `BatchOptions`); a derived node, an IRC end and a scan path's node take their own node's.
- `view_state` keeps `canvas` (the open one) and boxes in `expanded`.
- MCP tools `create_sub_investigation`, `move_to_sub_investigation`, `update_sub_investigation`, `list_sub_investigations`; confirm actions `delete_sub_investigation`, `dissolve_sub_investigation`.

### Frontend

- `subCanvas.ts` holds which canvas a record is on and how the open one shows it (`visibleCanvases`, `placeOf`: on its own card, by the outermost closed box on a drawn canvas, or outside).
- `Canvas.tsx` takes `canvasId` (the open canvas) and draws:
  - a box as `csub` (`SubBox`: + is Expand here, the arrow or a double-click is Open);
  - an expanded box's inside as React Flow children placed from its content's corner (`origins`);
  - an end outside the open canvas as a faded `stub` at the rim, level with the box it joins and its arrows on the facing sides (`StubNode`, D211, D212; a click opens its canvas through `onOpenRecord`).
- `App.tsx` keeps `canvasId`: `openCanvas` while the box exists, `reveal` opens a selected record's canvas, Esc goes up, the breadcrumb `.crumbs` in the canvas bar.
- `components/SubInvestigations.tsx` holds `SubInspector`, `NewSubDialog`, `MoveToDialog`, `BoxEndDialog` (an arrow drawn to a box ends at a node inside) and the Dissolve or Delete dialog.
- Elsewhere (FR-SUB-05): `subCanvas.placeText` ("in <box>", a transition by `transitionCanvas`, the innermost canvas holding both ends) and `placed` name where a record is in the overview's open items, the outline's node search, Ctrl K's node entries (group "Sub-investigations" opens boxes), the drawer's "Continue with" and the Analyses pickers. `openInside` is the card's open count. The outline's `SubInvestigations` section opens canvases.
- The read-only copy carries `canvas` in its view (`snapshot._view`, `ReaderView.canvas`) with the same breadcrumb, stubs and a box panel.

## Free species (D69, D72)

- A `Node` with `kind == "species"` is a substrate or fragment kept off the canvas. `TransitionSpecies` lists those that join or leave on a transition.
- `services/species.py` holds the rules: attach, kind changes, the balance added along a pathway, W-BALANCE. `pathways.profiles` and the energy view use it.
- `balances_from` gives each node's balance along the route from the reference (D72), for node cards (the energy view's `relative`) and for pathways that miss the reference.

## Resume overview (D191)

- `services/overview.py`, route `GET /api/overview` in `api/pathway.py`, shown by `components/Overview.tsx` whenever nothing is selected.
- D191: planned transitions fold into one closed line at the end of the open items (`plannedEdges`), so edges drawn between finished nodes do not bury what is still to compute.

## Remembered view (D105, A52)

- `InvestigationInfo.view_state` (migration 0018) holds the energy level, type and reference, edge energies, filters, expanded groups (and D193 boxes), `canvas` (the open sub-investigation, D193) and the energy drawer: open, tab, and since D190 the open energy profile's id (`profile_id`) instead of pathways. The shape is `ViewState` in `api.ts`.
- `services/view_state.py` cleans what is saved and drops deleted records when it is read.
- `GET`/`PUT /api/view-state` (quiet in `live.QUIET`, not an MCP tool).
- `App.tsx` restores it once per investigation shown (`shown`) and saves every change after that. The drawer's tab and profile id live in `App`'s `drawerView` (type `DrawerView` in `EnergyDrawer.tsx`, `ViewState['drawer']` without `open`), and the drawer starts from them (`saved`).
- Until the restore has answered (`viewRestored === shown`) the canvas bar and the work area are not drawn, so nothing chosen before it arrives is overwritten (`e2e/view-state.spec.ts`, "what is opened before the saved view arrives is kept").
- Switching notebooks (`resetInvestigation`): see "Notebook identity and ownership".

## Frontend main pieces

### App

- `App.tsx` holds the selection and the energy view (level, type, reference).
  - `reload` after a change fetches the canvas, the energy options and the energy view side by side, `loadView` keeping only the latest answer.
  - The history is fetched only while History is shown (`e2e/refetch.spec.ts`).
  - Moving or resizing a note patches `canvas.notes` in place.
- The app bar (D180): the investigation's commands in the `Menu` of `components/MoreMenu.tsx` on its name, the view tabs, the sync chip `SyncDialogs.SyncChip`.
- The canvas toolbar under it: Fit and Export are drawn into it by `Canvas.tsx` through a portal (its `tools` prop).
- `components/StartPage.tsx` (D182): the start page with the ways in, the recent investigations, and the Scan editor and Shut down set apart. `components/EmptyState.tsx` (D182): what an empty view says, with buttons to start.

### Menus and command palette (D184)

- `components/CommandPalette.tsx`: the command palette (Ctrl K) over nodes, sub-investigations, branches, steps and commands, and the keyboard shortcuts list ("?").
- `components/ContextMenu.tsx`: right-click menus at the pointer, with the entries of the shared menu lists (D179, D180).
- `menuKeys.ts`: the keys of the menu lists, shared by the button menus and the right-click menus.
- `panelActions.ts` (`PanelAction`): a panel's button row, which its record's right-click menu offers too.

### Canvas

- `components/Canvas.tsx` is the React Flow canvas, memoised (`CanvasPane`).
  - The app hands it handlers kept stable by `stable.ts`'s `useStable`, and the selection is added in a pass after the cards are built, so a click or an unrelated render of the app redraws no other card or arrow. Its own copy of the nodes keeps measured sizes.
  - Edge labels are placed clear of each other and of the cards by `labelLayout.ts` (D98).
  - An edge on the same side of two lined-up boxes bows out from it (`bowedOut`, D147).
  - `components/MoleculeSketch.tsx` (D43): the card's ball-and-stick picture in structure view mode, drawn as SVG.
- Tidy (D199) measures the boxes drawn at the top of the open canvas and hands them to `tidy.ts`, which lays them out in step columns, orders the columns against crossings and keeps them apart.
- Tidy with springs (D207), or in place (D208: ELK's interactive mode), hands them instead to `elkTidy.ts`: ELK's stress layout, arrow lengths from box sizes plus the `tidy_arrow_room` setting (D209), loaded through the `springLayout` prop so the read-only copy does not carry it. It turns the arrows' ends to face each other (`facingSides`, `TidySides`).
- `App.tsx` saves the positions and the sides and keeps the one-step Undo tidy.
- D210, expand or collapse all groups: `setGroupsOpen` in `App.tsx`. `groupsToOpen` is true when some group on the open canvas is collapsed. The controls are the canvas bar's Expand groups/Collapse groups button in `Canvas.tsx`, and the `groupsItems` entries "Expand all groups"/"Collapse all groups", offered in the command palette.

### Outline and side panel

- `Outline.tsx`: the steps, branches and search beside the canvas (D178: heads that fold, kept in localStorage; a step's actions on hover or focus, Alt+↑/↓ and drag by the grip; one-line node rows from `NodeRow.tsx`, shared with the read-only copy).
- `NodeInspector.tsx` and `PathwayInspectors.tsx`: the side panel. The node panel's sticky header (D179): stat strip with the card's ΔX from `cardEnergy.ts` (which `Canvas.tsx` uses too), the ⋯ `MoreMenu.tsx`, the jump bar over one scroll.
- In the node panel: `components/CalculationList.tsx` lists the node's calculations and results (`ResultValues`), `components/HistoryList.tsx` the history (also the History view), `components/Vibrations.tsx` picks a vibrational mode, imaginary first.

### 3D view and structures

- `Viewer3D.tsx`: the view-only 3Dmol viewer. `components/PopOut.tsx` and `popOut.ts` (D82): the pop-out 3D view, its place and size remembered per browser.
- `chem.ts`: xyz parsing, bonds, measurements, the card orientation (a quaternion matching 3Dmol's view rotation, D74) and which hydrogens to hide (D75).
- `display.ts` shares the hydrogens setting with every viewer and card.

### Energy drawer and saved profiles (D106, D189, D190, D194, D195, D205)

- `EnergyDrawer.tsx`: the pathways, profile and energy table below the canvas (the table drawn by `components/EnergyTableView.tsx`, shared with the read-only copy).
- Its height is dragged from the top edge, its list hideable, the profile zoomed; kept in localStorage (D106).
- D189: each pathway's "Continue with" is asked for again with `pathways.extend(..., follow=False)` after Undo step and whenever the canvas changes.
- D190: the pathways belong to the saved energy profile the drawer has open: `services/energy_profiles.py` (`EnergyProfile`, migration 0028, not in the history), routes `api/energy_profiles.py` (quiet in `live.QUIET`), MCP read tool `list_energy_profiles`. Repair `energy_profiles` makes the old drawer pathways "Profile 1". Saved on every change. The Analyses view lists them.
- Each pathway has `shown` and:
  - D194 `shifts`: a step moved whole columns later along the axis with every step after it (`ProfileChart`'s `shifts` prop).
  - D195 `colour`: picked on the pathway's swatch (`components/PathwayColour.tsx`). It wins over the branch's colour and the style's set (`profileStyle.pathwayColours(..., chosen)`, `ProfileChart`'s `chosen` prop).
- D205: the profile's own `reference_id` (migration 0031): the drawer's "Reference node", apart from the cards' reference in `App`. `onPathsChange` hands it to the read-only copy as `profile_reference_id`.

### Profile chart and style (D106–D111, D181)

- `ProfileChart.tsx` draws a profile in the app's profile style, set in `ProfileStyleDialog.tsx`. Its pure maths is in `profileAxis.ts`: the y range (`valueRange`), round ticks and their decimals, and text widths and wrapping (D198).
- D181: the drawer and the read-only copy pass `textScale={SCREEN_TEXT}` and `pointing`, for the hover tooltip, click to select and the selection's halo. PNG and SVG are saved from `profileFigure.figureOf`, a fresh render without either, so nothing on screen reaches them.
- D106: the setting `profile_style`, cleaned by `settings.clean_profile_style`; presets `PROFILE_PRESETS` mirrored as `SCREEN_STYLE` in `profileStyle.ts`, which also saves the PNG and SVG.
- D108: `edge_tags` hides the "no TS" tags, and a top-right legend moves below the title where the title, measured in the figure's font, would reach it.
- D109: `name_position: "bottom"` writes node names in a row under the plot.
- D110: labels of levels close together in one column stack above and below the cluster, never beside it.
- D111: a node several pathways share at the same energy is drawn in the text colour.

## Overlays (D80, A31)

- `services/geometry.place` fits on all atoms, on chosen atom pairs or not at all, optionally allowing a mirror image.
- `services/alignment.py` checks the atom lists (1-based, of the full structure) and keeps the named alignment sets (`AlignmentSet`, migration 0009; not in the history, A31).
- Routes `POST /api/overlay` and `/api/alignment-sets` are in `api/pathway.py`.
- `components/OverlayDialog.tsx` is the dialog. `Viewer3D` can hide a model, collect picked atoms and hand out a PNG.

## Notes pinned to node cards (D85, A35)

- `services/notes.py` keeps `NodeNote` (corner, colour, title, width, collapsed, and the text as a small HTML subset that `clean_body` rebuilds) and `NoteImage` (pictures named by their SHA-256; `clean_svg` strips scripts, handlers, embedded documents and outside links). Migration 0013.
- Changes are in the history under the node's id with record type "note".
- Routes are in `api/notes.py`. `GET /api/canvas` carries the notes, and the read-only copy carries the pictures as data URLs (`note_images`).
- Frontend: `notes.ts` mirrors the cleaning; `components/NoteContent.tsx` draws a note as React elements (never as inserted HTML) and holds the side-panel section; `components/NoteEditor.tsx` is the dialog with paste, drop and the ChemDraw advice.
- `components/CanvasNotes.tsx` draws them on the card: stacked in its corners or floating (D87, A37, migration 0014: `placement`, the offset from the corner, a line, `height`), dragged by the head and resized from the outer corner.

## Energies (EN-3, EN-4, EN-7)

### Values, levels and pathways

- `thermochem.py` computes quasi-harmonic G from stored frequency results. It mirrors the reference script; tests compare both. A linear molecule gets the linear rigid rotor (`_linear`, D164), checked against Gaussian on `HCN_singlet.out` and `gaussian_text.co2_freq`.
- `services/energies.py` resolves E/H/G/G_qh at a composite level key `levelId~geometryLevelId`.
- `services/pathways.py` builds pathways (also across edges drawn to or from a group: `_sides`, `_onward`, D108), profiles and the energy table (values pre-formatted, CSV uses the same strings, T-EN-08).
- Routes in `api/energies.py`.
- A node's energies come only from calculations for its own charge and multiplicity (D155, `NodeEnergies.other_state`); two states at one level give n/a, never the latest.

### Thermochemistry settings (D159, D95, D183)

- The G_qh temperature and cutoff and the 1 M standard state (D95) belong to the investigation: `InvestigationInfo.qh_temperature`, `qh_cutoff`, `standard_state`, migration 0023.
- `services/thermo_settings.py`: `settings(session)` is the app's settings with them in place; `update` records each change in the history as record type "thermochemistry".
- The app's values are only the default for new investigations, taken once by an older one through the repair `thermochemistry`.
- `/api/settings` reads and writes the open investigation's; `thermo_scope` and `thermo_defaults` say which.
- `components/ThermoSettings.tsx`'s `thermoSection` is its section of `components/SettingsDialog.tsx`, the two-pane Settings dialog of D183: sections under This computer and This investigation, a scope badge on each, a search, one row per setting with `NumberSetting`'s unit inside the field.
- `NodeEnergies` adds `thermochem.standard_state_correction` to G and G_qh, and the results name it (`standard_state`) so the labels can.

### Checks and W-* warnings (D154, D158, D162)

- Every `Value` carries `check` (D154): `NodeEnergies.check` judges the frequency job at the geometry level against the node's role (`frequency_issues`). Issues are listed in `check.issues`; profiles, the table's "Check" column, selectivities and turnovers pass it on.
- D158 W-FREQ-NONSTAT (`not_stationary`): the criteria Gaussian's table after a frequency job did not meet, kept in the result's `printed` under `parsers.common.NOT_STATIONARY`. Repair `frequency_stationary` for older imports.
- Also W-OPT-INC and W-TERM.
- D162 W-SPIN: the parsers keep ⟨S²⟩ in `printed` under `parsers.common.S2`, `S2_AFTER`, `S2_IDEAL`. `services/spin.py` judges it against S(S+1), also for the calculation's own warning and the import preview. `CalculationOut.spin` shows it. Repair `spin_values`.
- `warnings.node_warnings` checks the latest frequency job at each composite level (`NodeEnergies.frequency_jobs`), naming the level.
- `Canvas.tsx`'s group head shows its members' warnings (`memberWarnings`).

### Counterpoise and ONIOM (D161)

- `gaussian._parse_step` takes a `Counterpoise=N` step's corrected total (skipping the fragments) and keeps the BSSE as `printed["bsse"]` (`common.BSSE`).
- An ONIOM step gives its extrapolated energy, with the route word as its method (`gaussian.is_oniom`; `levels.canonical_method` per layer).
- `energies.bsse_of` reads the BSSE back. A counterpoise job counts as the uncorrected complex unless `Energies(bsse=True)` (`bsse` on `/api/energies/view` and `ProfileIn`; "With BSSE" in `App.tsx`, not remembered).
- Repair `counterpoise_energies`.

### Temperature, standard state and concentration (D160)

- G is the file's, at its job temperature, so `NodeEnergies._standard_state` takes its 1 M shift there (G_qh's at the G_qh temperature).
- W-TEMP (`check`, `Energies.temperature_note`) marks H and G at another temperature. `job_temperatures` (per level in the options, per profile, in analyses) name G's in `energyTypeName`, which always names the standard state.
- `species.standard_state_note` is W-STDSTATE (species at 1 atm in a solvation model).
- Profiles, tables and the energy view carry both as `notes` (`components/EnergyNotes.tsx`).
- A free species' own concentration is `Node.concentration` (migration 0024, `services/concentrations.py` with the `SOLVENTS` table, `components/SpeciesConcentration.tsx` in its panel).

### G model and frequency scale (D163)

- The parsers keep `g_model` (Gaussian RRHO, ORCA qRRHO, xTB mRRHO) and `frequency_scale` in a frequency step's `printed` (`common.set_thermo_model`; repair `thermo_models`).
- `services/thermo_model.py` reads them back (`of`; ORCA's frequencies are already scaled).
- `Energies.mixing` adds W-SCALE and W-G-MODEL to the check across a composite level.
- `CalculationOut.thermo` and the table's "Thermochemistry" column show them; `components/GModelBadge.tsx` names the model beside G.

### Printed-G pressure guard (S01, D57/D95/D160)

- `energies.printed_g_pressure_issue` rejects an explicit source pressure other than 1 atm before standard-state or species-concentration handling. The value is unavailable with W-PRESSURE in its message and check; source pressure, printed correction and calculation IDs remain accessible.
- `warnings.calculation_warnings` also shows the reason on frequency calculations and their nodes, including when G is absent from the options.
- E/H and G_qh keep their existing behavior: G_qh recomputes translation at 1 atm independently and receives no extra source-pressure shift. Missing source pressure keeps the existing input convention.
- `tests/test_pressure.py` checks 0.1/1/10 atm through the parser and direct SQLite services, standard states, species concentrations, composite sources, profiles/table checks, warnings and unchanged 1-atm balances and stored data.

## Selectivity (D83, A34)

- `services/selectivity.py` keeps the saved selectivities: `Selectivity` with `SelectivityOutcome` and `SelectivityMember` (a TS node or a group whose members all count); migration 0011; in the history.
- The result: one composite level and energy type with no fallback, the same atoms and charge, ΔΔG‡ and percentages both as a Boltzmann sum over each outcome's TSs and from its lowest TS, ee or de, the experiment.
- With a `reference_id` (D103, migration 0017) each TS is balanced by `species.balances_from` the reference, as node cards are, and the atom check includes the species.
- G_qh is recomputed at the selectivity's temperature (`Energies(temperature=…)`).
- Rejected, failed and superseded members are left out, and a duplicate member (geometry and energy) counts once (D156: `LEFT_OUT`, `_drop_duplicates`).
- Routes are in `api/selectivity.py`. `components/Analyses.tsx` is the Analyses view (a tab beside History); the selection inspector's "Selectivity…" makes one outcome per selected TS or group.

## Turnover (D86, A36)

- `services/turnover.py` keeps the saved turnovers (`Turnover`, migration 0012; the pathway as a JSON list of node and group ids, a comparison with another turnover; in the history).
- It computes the energetic-span model: the cycle from the node a closed pathway returns to, valued with `pathways.profiles` from its first node (so free species give ΔG_r), the full TOF formula, degrees of TOF control, TDTS, TDI and δE (`span_model`, tested against the exact steady-state rate).
- Routes are in `api/turnover.py`. The Analyses view lists turnovers below selectivities, and a closed pathway in the energy drawer has "Turnover". `ProfileChart` takes `marks` for the TDTS and TDI.

## Conformer equilibrium (D171, D173)

- `services/equilibrium.py` keeps the saved conformer equilibria (`ConformerEquilibrium`, migration 0026 `0026_conformer_equilibria.py`; in the history as record type "equilibrium") and computes the result.
  - Members are nodes and groups; a TS node is a barrier, every other node a conformer. `rate_matrix` (`eyring` rates) gives relaxation times, the time to equilibrium from `start_id` and populations against time.
  - D173, Curtin–Hammett: `next_ids` (migration 0027 `0027_next_step.py`) names the TSs of the step that follows, summed from the pool of the lowest conformer's set (`_next_step`).
- Routes `/api/equilibria` and `/api/equilibria/{equilibrium_id}/result` in `api/equilibrium.py`.
- MCP tools `list_conformer_equilibria`, `get_conformer_equilibrium_result`, `create_conformer_equilibrium`, `update_conformer_equilibrium`; confirm action `delete_conformer_equilibrium`.
- `components/Equilibrium.tsx` (`EquilibriumEditor`), listed in the Analyses view.
- Tests: `tests/test_equilibrium.py`, `frontend/e2e/equilibrium.spec.ts`.

## Buried volume and steric maps (D81, A32)

- `services/sterics.py` keeps the named steric profiles: `StericProfile`, migration 0010; per node `StericProfileAtoms` with the centre, orientation and left-out atoms and the last result with the inputs it came from, so it can say why a result is out of date; not in the history.
- It orients the structure in SambVca's frame, passes it to morfeus for %V_bur, quadrants and octants (the centre goes in as one extra point at the origin, so a centroid works) and computes the steric map itself.
- Routes are in `api/sterics.py`.
- `components/Sterics.tsx` holds the inspector's Sterics section, the settings and atoms dialogs (picking, `chem.fragment`), the map canvas and the comparison dialog, opened from the branch, group and selection inspectors and from each pathway in the drawer.
- `tests/fixtures/sterics/` holds morfeus's SambVca reference set.

## Step movie (D101, A48)

- The parsers keep each structure's SCF energy, its scan point and which structures an optimization converged on (`ParsedStep.geometry_energies`, `geometry_points`, `converged_geometries`, `scan`).
- Nothing is stored: `services/trajectory.py` reads the copied file again (`GET /api/calculations/{id}/steps`, not an MCP tool), merges a structure printed twice and turns each onto the calculation's final geometry.
- `components/StepMovie.tsx` is the player under the 3D view (passed as `Viewer3D`'s `below`, so it goes into the pop-out). `Viewer3D`'s `trajectory` shows one structure of a 3Dmol multi-frame model.

### Convergence criteria (D123b)

- `ParsedStep.geometry_criteria` holds per structure the `Criterion`s the optimizer printed for it: Gaussian's Berny table and predicted change on the last structure read; ORCA's "Geometry convergence" on its cycle's structure; xTB's output only its last cycle's energy change and gradient norm, on the final structure (`xtb.criteria` against `xtb.thresholds_of` or `OPT_LEVELS`).
- Carried by `trajectory.Frame.criteria` (a structure printed twice keeps the earlier table when the later has none) to the steps API.
- `StepMovie`'s `Convergence` is the table with a log-scale line per criterion.

### xTB scans (D112, A58)

- `xtb.parse_structures` reads an `xtbscan.log` or a scan path's `path.xyz` (comment lines `energy:`, optional `stage:` and a closing `call:` with the xtb command line) as one step of type other with route `xtb.SCAN_ROUTE`, scan "relaxed" and `ParsedStep.node_geometry` (`xtb.representative`: the highest top, else the middle point), which `final_geometry` returns.
- The level comes from `call:`, else from the xTB output beside it found by `imports.scan_outputs` (file browser only, `read_file`'s `folder`), else xTB's defaults with W-PARSE.
- "Use this structure" is `trajectory.use_frame` (`POST /api/calculations/{id}/steps/{frame}/use`, not an MCP tool): in place on a node whose calculations are all such scans (`changes_in_place`, also the scan's geometry and energy), else `nodes.derive`.

### Trimming a scan path (D117, A63)

- `Calculation.removed_points` (migration 0019) lists the scan points left out.
- `trajectory.trim_of` marks them on the frames and describes the kept path (`Trim`: top by `xtb.representative` over the kept points, the frame the calculation shows and whether that is the automatic top, cuts with `pathtools.rmsd` jumps, `pathtools._spikes`).
- `trajectory.trim` records one `removed_points` history entry and moves the node with `use_frame` when it showed the automatic top.
- `POST /api/calculations/{id}/steps/trim` and `/trim-preview` (quiet), not MCP tools.
- `StepMovie` hides removed points unless "Show removed", and its chart selects a section by dragging. `HistoryList.describeTrim` shows the points as runs.

## Inspect window (D123c, A70)

- `services/inspection.py` reads a file without importing it (`read`, `read_path`): `imports.read_file` plus `xtb.parse_optimization_log` for `xtbopt.log` (the xTB output beside it found by `imports.scan_outputs(folder, xtb.wrote_optimization_log)`) and its own reader for other xyz files. Each step's structures go through `trajectory.frames_of` (shared with the step movie). A parser failing on a cut-off end is retried without the last `CUT_BACK` lines.
- Routes `/api/inspections` in `api/inspect.py`: open (also a local job with `job`, D220, `_read_job`, the job's status in `job`, no import); `/refresh`; close; `/import` staging the copy as `/imports/from-path` or the remote import does. Page only, quiet in `live.QUIET`, not MCP tools.
- `Inspections` in `app.state` keeps each window's path and, for a server file, its temporary copy (`_copy_down`), closed on shutdown.
- Frontend `components/InspectWindow.tsx` uses `StepMovie`'s exported `EnergyChart` and `Convergence`; a scan's "Converged points only" as in the step movie (D125). Opened from:
  - `LocalJobsDialog`'s Inspect (D220: `InspectTarget.job`, status line, every 5 s while the job runs, no Import);
  - `ImportDialog`'s file browser (`onInspect`; "Inspect the N matching files" under a name filter);
  - `RemoteFilesDialog`;
  - the terminal's `inspect` message (`ServerPanel` → `SidePanel` → App's `inspecting`, a list of `targets` stepped through with Previous and Next file, D129).
- Copy XYZ copies the structure shown with `geometryToXyz` and `copiedComment` (D130). "Import this file" passes `ImportDialog` an `inspection`, whose copy App closes when that import ends.

## Scan editor (D135, A80)

- `ParsedStep.scan_coordinates` holds the coordinates a scan drives as the file names them (Gaussian's parameter table or ModRedundant S line, ORCA's `%geom Scan`, atoms from 1).
- `services/scan_editor.py`:
  - reads a file's scans (`scans_of`: each step with a scan and two or more converged points, `_points` by D125; the coordinate from the file, else `find_coordinate`; `_level` the fields compared, with ChkBasis not compared);
  - places a scan the right way round where it meets the path (`place`, `chain` from the start that joins the most, skipping a scan that `_turns_back`);
  - `join`s pieces (`Piece(scan, first, last)`, reversed when last < first) into frames fitted onto the one before, with the checks (`Mark`s) and the saved text (`file_text`, header `HEADER`; `read_joined` reads it back as one scan).
- `inspection.parse_text` and `read_text_of` are shared with Inspect.
- Routes `/api/scan-editor` in `api/scan_editor.py` (`ScanEditors` in `app.state`, closed on shutdown). Targets on this computer, on a server (copied as Inspect does) or an imported calculation's stored copy. Page only, quiet in `live.QUIET`, not MCP tools.
- Frontend `components/ScanEditor.tsx` (list, `ScanChart`, strip, player; undo and redo of the pieces), opened from App's `openScanEditor`: header and start page buttons, `ImportDialog`'s `onScanEditor` through `FileBrowser`'s `matching`, `InspectWindow`'s `onScanEditor`, and the `OpenScanEditor` context that `Displays` provides for the step movie.

### Finer scans, crossings and comparing (D136)

- `finer_offers`/`replace` (a finer scan over the path's frames, `between` a stretch), `crossings`/`cut_at_crossing`, `compare` and `match_atoms` (`atom_matching.match` at the best-fitting ends, else `_by_distances`).
- `_same` also compares the scanned coordinates; `_points` stops where the scan's point numbers restart (`Scan.restarted`).
- Routes `/path` (`replace`, `between`, `crossing`), `/compare`, `/scans/{key}/match`.

### Importing a joined scan (D137)

- `parsers/joined.py` reads the saved file for the import (`imports.read_file`, route `joined.ROUTE`, `ParsedStep.basis_fingerprints` named by `levels.find_basis` in `_resolve_level`, else a blocker).
- `trajectory.is_scan_path` (and `is_joined_scan`) and `scan_edges.has_scan_path` count it as a scan path; `energies.NodeEnergies` leaves it out; `PathOnEdge.program` names the chip.
- The editor's Import… goes through App's `ImportRequest.pathEdge` to `ImportDialog`'s `pathEdge` (options `path_edge_id`, `on_edge_only`), the editor `hidden` meanwhile.

### From a server (D138)

- The `scans FILE` command, refresh and save to the server: see "SSH servers such as Saga".

## IRC of a TS node (D131, A76)

### Records and import

- `Calculation.irc_part` ("path", "end_opt", "end_freq") and `irc_direction` (migration 0021) mark a TS node's IRC parts.
- `Node.calculations` leaves them out (`Node.irc_calculations` holds them), so nothing that reads a node's calculations sees them. `nodes.calculation_count(..., own=True)` is the count the panel shows.
- `nodes.calculation_counts` gives every node's in one query. `GET /api/canvas`, `/api/nodes` and the overview load nodes with `list_nodes(with_calculations=True)` and read `thermo_settings.settings` once, so they ask a fixed number of queries whatever the number of nodes (`tests/test_canvas_queries.py`).
- The Gaussian parser reads an IRC step's points (`_IrcReader`: CURRENT STRUCTURE, else the last structure before "Point Number"; the checkpoint's TS put first) into `ParsedStep.irc` (`IrcRun`, `IrcPoint`).
- `imports._plan_irc`/`_commit_irc` import a file as parts when `ImportOptions.irc_part` is set (`Plan.kind == "irc"`; `_irc_basis` gives a ChkBasis file the TS optimization's basis set).

### Reading it back

- `services/irc.py` reads the parts back from the copies (`read`: runs per direction, continuation and replacement, the mass-weighted distance after `geometry.place` onto the TS, end-point energies relative to the TS node; `summary`; `remove` is `import_undo` of the file).
- Routes `/api/nodes/{id}/irc`, `/irc/summary` and `DELETE /irc/files/{source_file_id}` in `api/irc.py`. `get_irc` is the MCP read tool (`structures=true` adds each point's coordinates).
- Connection check (D132, A77): `irc.read` judges each end (its optimization, else the last point) against `_neighbours` (nodes on edges touching the TS or its group) by the bonds among `pathtools.active_atoms`, and sets `Side.connection` and `Side.same_as` (`imports._duplicates`). `_left_side` puts the end reaching an incoming edge's node on the left (points reversed and negated).
- `irc.make_node` (POST `/irc/{direction}/node`) re-imports the end point's files as a new node.
- `components/IrcWindow.tsx` is the window (slots, `IrcChart`, player, energies table; imports through `ImportDialog`'s `irc` prop), opened by IRC… in `NodeInspector`.

### ORCA (D133, A78)

- `orca._read_irc` reads an IRC step's directions and endings from the output and its points from the forward and backward trajectories it names, through `parse`'s `companion` (`imports.beside(folder)`: the folder it was picked in, or the copy's folder in `trajectory._read`).
- `IrcRun.files` holds their contents, which `_commit_irc` copies beside the output; `IrcRun.missing` the absent ones, which block the import.

### Routing (D134, A79)

- `imports.plan` gives a file with an IRC `Plan.irc_targets` (`imports.irc_targets`: TS nodes its TS point fits within the duplicate tolerance) and a blocker. The import dialog's "Add to the IRC of …" re-previews with `irc_part`.
- `batch_import` orders IRC files last and imports each with `irc_part="path"` into its TS (row `kind`/`match` "irc", `how` "irc").
- `api/remote._with_trajectories` copies an ORCA IRC's trajectories down beside it (`orca.irc_trajectories`). The IRC window's "From <server>…" opens `RemoteFilesDialog` with `purpose` (one file, no batch).
- Tests: `tests/gaussian_text.irc` and `end_point_opt_freq`, and ORCA's sample in `tests/fixtures/orca/irc/`; Jonas's own IRC files are never committed.

## Edit structure window (D139, A84)

### Tools and routes

- `services/builder.py` holds the tools on a `Structure`: rows, plus the user's `added` and `removed` bonds on top of the 3D view's rule (`atom_matching.bonds`), which only decide which atoms move together and are never saved.
  - `sides`: the atoms reached from an end without passing the picked atom next to it; joined sides fall back to the end atom and its terminal atoms.
  - `set_distance`/`set_angle`/`set_dihedral` (IUPAC sign, as `chem.ts` and `pathtools`), `rotate_about`, `set_bond`, `change_element`, `delete`, `add_atom` (`_free_direction`), `replace_with_fragment` (`FRAGMENTS`), `add_molecule` (`MOLECULES` or a node's rows), `electrons_note`.
- Routes `/api/builder` in `api/builder.py` store nothing (quiet in `live.QUIET`; the window's own, except `/run` and `/check`, D144).
- The window saves through `PUT /api/nodes/{id}/geometry`, whose `new_node` always derives.
- `components/StructureEditor.tsx` is the window (picks, undo and redo, `Viewer3D`'s `keepView`), opened by XyzEditor's "Edit in 3D…" (`NodeInspector`'s `onEditStructure`, App's `editingNode`).

### Holds and Minimize (D140, A85)

- `Structure.changed`: atoms changed in the window, kept by every tool and renumbered by `delete`.
- `Structure.holds` (`Hold`: one atom frozen, or 2 to 4 atoms held at a value; `freeze`, `hold`, `release`); a set coordinate moves its hold.
- `services/minimize.py` is RDKit's UFF with bond orders found per ligand without the metals (`_molecule`, `_bonds` dropping bonds past an atom's `VALENCE`). Metals are held in shape by restraints (`_hold_metals`; a bond the user added to a metal at the radii sum), holds become restraints and frozen atoms are fixed. It moves `_near` (two bonds round the changed atoms) or everything (fitted back). It is the `minimize` tool of `/api/builder/edit`.

### Minimize again stays put (D145, A90)

- `Structure.minimized_from` (set by Minimize, dropped by `Structure.but` with new rows) gives the bonds, the metals' shape and their clearance (`_clear_metals`, always on), so the next run starts at the same minimum. The window carries it as `minimized_from`.

### Relax metals (D146, A91)

- `minimize.MAIN_GROUP` joins `TYPED` (held where `_typed` fails). `minimize(..., relax=True)` calls `_relax_metals`.
- Its targets come from `services/metal_shapes.py`: `UFF` radii and electronegativities as RDKit carries them, `bond_length`, `bond_order`, `nearest_shape` over `SHAPES`, `relaxed`. A metal it returns None for is held.
- `Structure.minimized_metals` says which mode `minimized_from` belongs to. The window's "Metals" radios and `metals` on `/api/builder/edit` and `build_structure`.

### From nothing and SMILES (D141, A86)

- `add_atom` with nothing picked puts a lone atom; `add_hydrogens` (`USUAL_BONDS`).
- `services/smiles.py` (`build`: RDKit ETKDG from `SEED`, MMFF or UFF; `as_fragment` for a SMILES with one `*`).
- The `smiles` and `hydrogens` tools, and `fragment` with `smiles`.

### Flip (D166)

- `builder.flip` (Flip in the window with three atoms picked, and the `flip` step with `ignore_bonds`) swaps atoms 1 and 2 by 180° about the line from their middle towards atom 3.
- `flip_plan` finds what turns and the tie (an atom bonded to 3 off the fragment's side, `FRAGMENT_SIDE`), which `/describe` returns as `flip` for the window's tick boxes.

### Measurements (D151)

- `services/measurements.py` (`table`: rows with `source_of`, the D100 optimization; numbering by `atom_matching.match` to a reference or `Finder`'s SMARTS on `minimize._molecule`; `_summaries`, dihedrals by `_round_mean`).
- `POST /api/measurements` in `api/measurements.py` (MCP read tool `measure_nodes`, quiet in `live.QUIET`).

## Ketcher drawer (D142, A87)

- `components/KetcherDrawer.tsx` is Ketcher (`ketcher-react` with `ketcher-standalone`'s `binaryWasm` build, Indigo as WebAssembly in the page).
  - Imported lazily by `StructureEditor.tsx`, so neither the main bundle nor the read-only copy carries it.
  - `ketcherGlobals.ts` gives it Node's `global` and the `events` package its EventEmitter.
  - `BUTTONS` hides its 3D view, reactions, shapes and text. It sets `window.ketcher`, which the UI test uses.
- Draw… in the window's "Add a molecule" opens it over the window; the drawing is kept as KET while the window is open.
- Convert to 3D is the `drawing` tool of `/api/builder/edit` (`molfile`): `smiles.from_molfile` reads the MOL file (an attachment point, `M  APO`, or an `R#`/`*` atom makes a fragment) and shares `smiles._built` with SMILES. `smiles._metal_bonds` sets every bond to a metal to the radii sum (`builder.set_distance`, ligand side) and Minimizes everything.
- The shape drawn round a metal (D142f): `read_drawing` keeps each atom's 2D place (`drawn_x`, `drawn_y`); `_metal_shape` reads trans pairs from it (`TRANS`) and gives each bond an octahedron corner (`_corners`); `_placed` moves each single-donor ligand onto its corner (`_face_metal`, `_clear`); `minimize.minimize(..., clear=True)` keeps unbonded atoms off the metal.

## Draw from this structure (D143, A88)

- `services/redraw.py`. `to_drawing` (route `POST /api/builder/drawing`) draws the structure as `minimize._molecule` sees it: `_drawable` (neutral ligands dative to the metal, anionic ones plain), `_radicals` for missing H, `_layout` picks RDKit or CoordGen, `_no_valence` drops a metal's VAL, which Ketcher would fill with H.
- The `redraw` tool (`from_drawing`) matches the drawing back by MCS piece by piece (`_match`, on `queryMol`), keeps matched atoms and their H (`_changed`), embeds new atoms with the matched ones round them held (`_region`, `_embed`), deletes the rest and Minimizes.
- The window's "Draw from this structure…" opens `KetcherDrawer` on it (`drawing` state `{molfile}`).

## Calculations on this computer (D213–D220, FR-LOC)

### Tools install and g-xTB (D213, D216)

- `local_tools.py` keeps the tools folder (`tools_dir()`: the data dir's `tools/`, or `<config>/tools` under `CHEMBOOK3D_CONFIG_DIR`, or `CHEMBOOK3D_TOOLS_DIR`).
- It downloads micromamba (`MICROMAMBA_URL`, `MICROMAMBA_SHA256`) and builds `env-new/` from `assets/local-tools/<platform>.txt` (explicit conda-forge list with MD5s, made by `scripts/make_local_tools_lock.py`), checks it (`check`), writes `env/chembook3d-tools.json` and swaps it in.
- `Installer` runs that in a thread (`app.state.local_tools`, held in `Lifetime.holds` so a launched app waits for it). `python -m chembook3d.local_tools install` does it without the app (CI).
- g-xTB (D216) is a second install (`install_gxtb`, `Installer("gxtb")`, `GXTB` pins per platform, `gxtb_binary`), offered as the method `gxtb` once there (`install-gxtb` for CI).
- `local.json` there holds cores and memory (`resources`).

### Jobs and the worker

- `local_jobs.py` writes `local-jobs/<id>/job.json` and copies `local_worker.py` there as `worker.py`. `_spawn` starts it detached (new session on Linux; `DETACHED_PROCESS`, breakaway from uv's job object on Windows) with the environment's PATH, thread counts and `PSI_SCRATCH`.
- `job_status` reads `progress.json` (heartbeat every 5 s, alive for `ALIVE_S`), `result.json` and `cancel`. `cancel` kills the process when its heartbeat is fresh.
- `estimate` gives each method's time for a structure from `TIMING` (70 atoms, 4 cores) or this computer's own seconds per step (`calibrate` after each import, kept in `local.json` by `local_tools.save_calibration`).
- `import_result` imports `result.json` onto the job's node with `imports.commit` (parser `parsers/local_result.py`, recognised first in `imports.read_file`).
- `local_worker.py` runs only in the tools' Python (stdlib, numpy, geomeTRIC, tblite, Psi4): the queue lock (`Queue`, `local-jobs.lock` in the tools folder); `make_calculator` per method from `_engine`: `stub`/`stub-low`/`stub-saddle` for tests, `gfn2`, `gxtb` (runs xtb, `read_xtb_gradient`), `b97-3c`, `r2scan-3c`. Psi4 reads the last step's orbitals as its guess.
- geomeTRIC's custom `Engine`; constraints as geomeTRIC's `$freeze`/`$set` text (`read_constraints`).

### ONIOM (D214)

- `services/oniom.py` suggests the DFT core (`suggest`, `close`) and checks it with its link atoms (`build` → `Core`).
- `local_jobs.LAYERED` names each layered method's high and low engine. `create_job` stores `oniom` {core, links, high, low} in `job.json` and puts the core's size in the level.
- In the worker, `oniom()` runs over three engines, with `model_coordinates` placing the link hydrogens.

### TS search and frequencies (D215, D217)

- A TS search (`job.json` `transition`) hands geomeTRIC a `numerical_hessian` of the `ts_hessian` engine (GFN2-xTB; `stub-saddle` in tests) as `hessian.txt` and checks the result with `mode_check` (`result.json` `ts_check`). The final check keeps its `mode`, which `job_status` sends with `final_xyz`.
- Frequencies at the result (`job.json` `frequencies`, set by `create_job` for `local_jobs.FREQUENCY_METHODS`; `OWN_HESSIAN` methods start a TS search from their own Hessian) are `harmonic` of the job's own engine's `numerical_hessian` (`result.json` `frequencies`).
- `parsers/local_result.frequency_step` imports them as a second, frequency step (`rotor` gives the mass and rotational temperatures).

### Scans and the 2D grid (D218, D219)

- A relaxed scan (`job.json` `scan` {coordinates, points}, checked by `local_jobs.check_scan`) runs one optimization per point of `scan_values` (`result.json` `scan.points`, `frame_points`).
- `parsers/local_result._scan` reads it as a relaxed scan; `_import_result` imports it as a new node (`scan_name`, `SCAN_OFFSET`).
- The dialog's Scan section is read by `readScan`, and a finished one told by `scanText` in `localJobs.ts`.
- A 2D grid (`scan.grid`, `local_jobs.GRID_METHODS`, `_check_grid`) runs in `local_worker.grid_cells` order. The parser sets `ParsedStep.scan_grid`, which `trajectory.Steps.grid` and the steps API carry to `components/SurfaceMap.tsx` in `StepMovie` (colours by `display.ramp`, shared with the steric maps). `scan_editor.scans_of` skips it.

### Live Inspect (D220)

- The worker's `record_criteria` wraps geomeTRIC's `Optimizer.evaluateStep` and writes each step's five criteria to `criteria.jsonl` (and `result.json` `criteria`).
- `local_jobs.inspect_data` gives the result, or for a running job trajectory.xyz's whole frames (`_trajectory`) and `_criteria` in the result's form with state `local_result.RUNNING`. It is read by `local_result.parse_data` (criteria into `ParsedStep.geometry_criteria`, so the step movie shows them too) and `inspection.of_parsed`. Local jobs' Inspect opens it (see Inspect window).
- A scan's frames carry `point P` in their comment, and each finished point is appended to `scan-points.jsonl`. A running grid gets every cell (`grid_cells`), so `SurfaceMap` fills in (`pending` cells left empty).
- `local_jobs.scf_progress` reads the end of psi4.out for the SCF of the DFT/ONIOM step in progress (`progress.scf`, told by `localStatusText`).

### Routes, frontend and tests

- Routes in `api/local.py`: `/api/local/tools` not offered to Claude; jobs are MCP tools `*_local_*`; `POST /api/local/core` is `check_local_core`, which also gives `mode_atoms` from `local_jobs.mode_atoms`.
- `localToolsSection.tsx` (the Settings section) and `components/LocalTools.tsx`.
- `components/RefineDialog.tsx` (node menu and the panel's ⋯ menu): the ONIOM core editor with its 3D view in `components/RefineCore.tsx`; constraints read by `readConstraints`, and D165's earlier ones by `heldBefore` in `localJobs.ts`.
- `components/LocalJobsDialog.tsx` (canvas toolbar, palette; Show mode animates the final TS check's mode, D217).
- `localJobs.ts` (`tsCheckText`, `progressText`; `useLocalJobs`: reads every 3 s while a job runs, else every minute, and imports finished results).
- Tests: `tests/test_local_jobs.py`, `tests/test_local_oniom.py`, `tests/test_local_ts.py`, `tests/test_local_gxtb.py` (stub method with the test's Python via `CHEMBOOK3D_LOCAL_PYTHON` and `CHEMBOOK3D_LOCAL_STUB`; the real tools with `CHEMBOOK3D_REAL_TOOLS=1`, CI job "Local tools"), `e2e/local-jobs.spec.ts`.

## Cloud calculation jobs (D93, A40)

- `cloud_jobs.py` writes `jobs/<date>-<name>/` (`job.md`, `job.json`, `inputs/`), commits only that folder and the cloud files of `cloud_templates.py`, and pushes. The cloud files: `.claude/settings.json` SessionStart hook, `.claude/chembook3d/setup-tools.sh` installing pinned xtb and crest, `.claude/CLAUDE.md` with the rules the cloud session follows.
- It runs `claude --cloud` in a `claude_panel.Terminal` (it refuses without a TTY; `--permission-mode auto`).
- `Launcher` reads the session id and link from the screen and answers no questions. A launch whose screen stays still is `waiting`; the Claude panel shows its screen through `GET /api/claude/launches` and the `/api/claude/launch-view` WebSocket (`components/LaunchQuestion.tsx`), so the user answers there.
- The cloud instructions name the repository's https address (`cloud_templates.github_https`) because an uploaded folder has no origin. `send_message` passes Claude's message to `claude -p --cloud <session>` on its input.
- Results are found as `jobs/<id>/result.json` on any branch (`_results_on_branches` and `_read_results`: one `for-each-ref` and one `cat-file --batch-check`, then one `cat-file --batch`, for every job at once). `job_statuses` lists them with these, so `GET /api/jobs?detail=true` costs a few git calls whatever the number of jobs and branches. Only `outputs/` and `result.json` are copied back.
- The open investigation's check for finished scan paths (`cloudJobs.ts`) never starts while the last is still going.
- Routes in `api/jobs.py` (quiet for the change counter, refused from foreign origins); MCP tools `*_cloud_job*` in `mcp_tools.py`; the hand-off steps in the panel's CLAUDE.md (`claude_panel.NOTEBOOK_GUIDE`).
- Tests use `tests/fake_claude_cloud.py` and a local bare repository.

## Scan path job (D114, A60)

### Ends, atoms and the job

- `services/scan_path.py` resolves the ends (a group stands for its representative or a chosen member; an edge through a member's group counts), checks them and matches atoms (D113).
- It suggests the coordinates to hold at a TS end: the imaginary mode, else the guess's partial bonds for the user to tick.
- It writes a D93 job with `create_job(..., extra)`: `kind: "scan_path"` in job.json; inputs start.xyz, end.xyz renumbered, mapping.json, path.json; instructions in `INSTRUCTIONS`.
- `pathtools.py` (standard library only; atoms from 1 everywhere, dihedrals in xTB's and IUPAC's sign as `chem.ts`; commands diff, rmsd, measure, frames, trace, pathfinder, join, check, mode, displace) is written into the repository as `.claude/chembook3d/pathtools.py` with the cloud files (and a `.gitignore` for `__pycache__/`) and tested against the app.
- Routes `POST /api/scan-paths/plan` (MCP read tool `plan_scan_path`) and `POST /api/scan-paths` in `api/scan_paths.py`. `components/ScanPathDialog.tsx`, opened by the selection inspector's "Scan path…".

### The brief and the coordinates (D116, D118, D119, D196)

- Brief and TS mode (D116, A62): `INSTRUCTIONS` carries xTB's `$scan` crib, the carried restraints, hold-then-drive, the strategy order (downhill from a TS first, `TS_FIRST`), the budget and the `gate`. `Plan.modes` sends a TS end's mode as `inputs/<end>_mode.json` (turned with the end, `_turned`). Held rows carry `other_value`. `Plan.warnings` warns when no bond changes. `_notes` writes a missed gate, the conformer note and the TS check.
- Reacting atoms (D118, A64): `pathtools.active_atoms` (two bonds out) goes into path.json as `active`. `check(..., active)` judges the end on them (`active_end_rmsd`); `diff` gives `rotations` and a `scan_block`; `trace_input` reads a stage's scan.inp; `path_finder` converts `xtbpath.xyz` (kcal/mol) to Eh; `_notes` gives both RMSDs and names `outputs/feedback.md`.
- The user's own coordinates (D119, A65): `scan_path.check_drive` checks the dialog's "Drive these" rows into `drive` (path.json and job.json; `order` together or staged, each row `from` and `to`). The brief runs them first (`DRIVE`, `USER_FIRST`) and asks for `path.user_strategy`, which `_notes` reports. `pathtools.followed` is `check`'s `drive` report, and `pathtools._side_on` lets `diff` list the turn of an η² ligand about its metal.
- Coordinates kept fixed (D196, A131): `scan_path.check_constrain` checks the dialog's "Constrain these" rows into `constrain` (path.json and job.json; each row `value`, the start's by default; never a held, driven or changing bond). The brief's `CONSTRAIN` (`_constrain_brief`, refilled by `_wrapped`) puts them in every xtb call. `pathtools.kept` is `check`'s `constrained` report (a path that lets one go is not `good`), and `_constrain_note` names them in the import notes.

### A free species joining or leaving (D120, A66, A67)

- `services/scan_species.py` reads the edge's one species in the path's direction (`on_edge`).
- It matches the side without it, put `beside` the species, to the bound side in three passes (`matched`: species heavy atoms, then complex and species apart).
- It builds the separated end (`separate`: own geometries fitted, the species pulled out along the line from what it binds to until `clearance`).
- `scan_path.plan` keeps it in `Plan.species`, `Plan.start_rows` (the path's start in the start's numbering) and `Plan.match_rows` (what the review shows). The dialog's `ScanPathSpecies.tsx` shows the structure apart. The atoms the species binds to are `loose` in `atom_matching.match` (a metal may change shape).
- Sent (A67): the separated end is `start.xyz` or `end.xyz`; `path.json` gets `species` (`_species_settings`, with `scan_species.approach`); `complex.xyz` and `species.xyz` go along; the brief gets `SPECIES` and `SPECIES_FIRST` (refilled by `_wrapped`). `pathtools.check(species=, apart=)` judges the separated end (`separated_end`, `--apart` for `leftover_kcal`), and `_species_note` names it in the import notes.

### Coming back (D115, A61)

- `scan_path.import_result` (`POST /api/jobs/{id}/import-path`, MCP write tool `import_scan_path`; `again` only after its node was removed) fetches the result and imports `outputs/path.xyz` through `imports.commit` as a new node (`ImportOptions` carries `step_id`, `branch_id`, `notes`), recording `imported` or `import_error` in job.json (`cloud_jobs.update_job`).
- `GET /api/jobs?detail&refresh` fetches once for all.
- In the frontend `cloudJobs.ts` (`useScanPathImports`, mounted in `App`) checks every `CHECK_EVERY` ms, and `components/CloudJobsDialog.tsx` is the header's "Cloud jobs" list.

### Paths on edges (D121, A68, D200)

- `Node.path_edge_id` (migration 0020, in the history) is the edge a path was run on, set by the import (`ImportOptions.path_edge_id`, with `on_edge_only`) or by hand (`PUT /api/nodes/{id}/path-edge`, MCP write tool `link_scan_path`). Linked once for older cloud jobs by the repair `scan_path_edges`. `Node.on_edge_only` is layout.
- `services/scan_edges.py` holds the rules:
  - `on_edge`: the box is off the canvas: set, linked, still `changes_in_place`, no edges or group of its own;
  - `release` (called by `transitions.remove`): unlink, box back halfway;
  - `after_import`: a calculation that is not a scan path brings the box back;
  - `on_edges`: each edge's `scan_paths` (the top is `Trim.barrier`, the gate and RMSDs from the job's result.json) in `TransitionOut`, which never feed an energy.
- D200, A134: a Gaussian or ORCA scan imported as its own node (`trajectory.is_held_scan`/`names_scan` read D157's constraints) is linked the same way. `scan_edges.is_path` and `paths_only` take the place of `is_scan_path` and `changes_in_place` there, and `_top` measures its chip over its converged points (it has no `Trim`).
- Frontend: `pathChip` in `api.ts` is the chip `Canvas.tsx` draws (clicks go through the `PathChipClick` context: select the edge, or in the read-only copy open the path node). `components/ScanPathsOnEdge.tsx` holds the edge panel's "Scan paths" (a `StepMovie` started on `calculationId`) and the node panel's "Shown on an edge".

## SSH servers such as Saga (D122, A69)

### Keeper and login

- `remote/servers.py` keeps the saved servers in `remote/servers.json` in the config folder (Saga first, nothing secret).
- `remote/keeper.py` is the keeper, `python -m chembook3d.remote.keeper --config <folder>`: a process of its own on 127.0.0.1 that holds each server's asyncssh connection and one shell.
  - `SHELL_COMMAND`: bash with the login files and a `PROMPT_COMMAND` printing OSC 1337 `CurrentDir`, read back by `Shell._track`.
  - The last `OUTPUT_KEEP` (1,000,000) characters of output are kept for `attach`.
  - It answers one JSON line per request after checking the token in `remote/keeper.json` (mode 600).
  - It logs in by the server's own keyboard-interactive or password prompts (`Login.ask`: one answer per prompt; a repeated prompt ends the login as refused; no keys or agent).
  - It checks host keys against `~/.ssh/known_hosts` and `remote/known_hosts`, logs out after the idle time and ends when nothing is left.
- `remote/client.py` is the app's side (`ensure` starts it detached, outside uv's job object on Windows when allowed; `call`, `attach`).
- Routes `/api/remote/...` in `api/remote.py` (the Claude panel's local checks plus a required Origin; quiet in `live.QUIET`; not MCP tools).
- Frontend: `components/SidePanel.tsx` holds the side panel's tabs (Claude, each logged-in server, and the HPC tab with its Cluster list and "Add a cluster…", D127; `hpc.ts` has the header button's `hpcLabel` and Sigma2's `CLUSTER_PRESETS`). `components/ServerPanel.tsx` holds a server's login steps and terminal, and `terminal.ts` the xterm.js setup both tabs share.

### Import from here and Send to <server> (D122e, D122f)

- Import: the keeper's `list` and `download` ops over SFTP. `/files`, `/import` and `/batch-import` copy into a temporary folder and stage through `imports.Staging.add(..., device=)` or `batch_import.scan(..., origin=, shown=, device=)`, so the origin is `<server>:/path` and `last_device` is left alone.
- `components/RemoteFilesDialog.tsx` picks the files and hands them to `ImportDialog`/`BatchImportDialog` through their `remote` prop.
- Remote downloads use `remote/keeper.py`'s `_download_destination`: POSIX basenames must be valid ordinary Windows filenames too, and the resolved destination must remain in the chosen local folder. Original remote paths and safe companion names stay intact. `tests/test_remote_download_paths.py` covers traversal, devices, alternate streams and ORCA trajectory names.
- Send: the keeper's `upload` op (exclusive create unless `replace`), route `/send`, buttons in `components/XyzEditor.tsx`.

### X11 windows (D123a, A70)

- `Server.x11` and `x11_display` (`servers.forwarding`), passed to the keeper's `login` and `shell` ops as `x11` (`Session.x11`; `shell` with `restart` replaces a running shell).
- `open_shell` asks asyncssh for `x11_forwarding="ignore_failure"`, and `status` reports each shell's `x11` and `x11_ready` (`keeper.x11_answers`). Keeper `PROTOCOL` 2.
- The tab's X11 row in `ServerPanel.tsx`.
- `FakeSaga` forwards X11 (`x11=False` refuses), its shell's `xclient` opens the display, and `FakeXServer` stands in for Xming.

### The inspect and scans commands (D123c, D129, D138)

- `inspect FILE` is defined in `keeper.RC` and prints OSC 1337 `Inspect=<full path>`. `Shell._track` returns every `REPORT_MARK` report in order, and `feed` gathers the files one command names. `_send_inspect` sends them as one `{"type": "inspect", "paths": [...]}` when the prompt's `CurrentDir` comes back, or after `INSPECT_WAIT` (D129, keeper `PROTOCOL` 6; live only, not replayed). `status` reports `inspect`; keeper `PROTOCOL` 3. `FakeSaga`'s shell has `inspect` too.
- D138: `keeper.RC` defines `scans FILE` (OSC 1337 `Scans=`, `keeper.ASKED`; `Shell._send_inspect` sends each kind as its own message, `{"type": "scans", "paths": [...]}`). `ServerPanel`'s `onScans` → `SidePanel`'s `onScanEditor` → App's `openScanEditor`.
- `RemoteFilesDialog` has `onScanEditor` ("Scan editor (n)") and `onAdd` (the editor's "Add from <server>…").
- `POST /{id}/refresh` reads the files again (`scan_editor.refreshed` keeps `Scan.order`, `carry` moves the pieces), and `POST /{id}/save-to-server` calls the keeper's `upload` with `free` (`Keeper._upload_free`). Keeper `PROTOCOL` 7.

### Claude reads server files (D124, A71)

- `api/server_files.py`: routes `/api/server-files/...` open to loopback callers without an Origin (the MCP read tools `list_servers`, `list_server_files`, `read_server_file`, `summarize_server_file`, `get_server_structure`).
- The keeper's `read` op (a byte range, `MAX_READ`) and `within=True` on `list` and `download` resolve every path with the server's realpath and refuse one outside the shell's current directory (`keeper.within`, `Outside`). Keeper `PROTOCOL` 4, checked by `READER_PROTOCOL`.
- `Copies` (in `app.state.server_copies`) keeps whole-file copies for search, deep pages and summaries by size and time, dropped by the `logout` route and at shutdown.

### Claude writes new files (D126, A72)

- The page-only route `PUT /api/remote/servers/{id}/claude-writes` sets the keeper's `writes` (per login, `Session.writes`). `status` reports `writes` and `written`, shown as `claude_writes`/`claude_written` and the "Let Claude write new files here" row in `ServerPanel.tsx`.
- `POST /api/server-files/{id}/write` (MCP tool `write_server_file`, `ToolSpec.allowed_in_app`, so in `mcp_tools.PANEL_ALLOWED`) calls the keeper's `write` op (`Keeper._write`: exclusive create, new folders, `WRITE_PART` names, `_free` copy names, a `written` message to the terminal) and, with `based_on`, returns the difference. Keeper `PROTOCOL` 5, checked by `WRITER_PROTOCOL`.
- Tests: `tests/fake_ssh_server.py` (`FakeSaga`: password then code; a tiny shell, or the real bash on Linux); the UI tests run it on port 8767.

## Claude in the notebook (D91, A38)

- `mcp_server.py` is `chembook3d mcp` (stdio, the official `mcp` SDK's low-level server). It turns tool calls into requests to the running app at 127.0.0.1 with the header `X-Chembook-Client: claude`.
- `mcp_tools.py` lists the tools (read, write, confirm) and builds their input schemas from the app's own OpenAPI description, so a new route needs a tool or an entry in `tests/test_mcp.py`'s NOT_OFFERED.
- The full list of MCP tools is `mcp_tools.TOOLS`, and of confirm actions `mcp_tools.CONFIRM_DESCRIPTIONS` (run by `api/live.py`'s `ACTIONS`).
- `api/live.py` holds the app side:
  - `ChangeTracker` counts every successful change by client and sets `history.SOURCE` to "claude" for Claude's requests.
  - `GET /api/live` is the long poll open tabs wait on (`frontend/src/live.ts` reloads on a change made elsewhere).
  - `PUT/GET /api/selection` is what the tab shows.
  - `/api/confirmations` holds Claude's delete requests (`ACTIONS`), which the app runs in-process only after the page's Confirm (`components/ClaudeRequestDialog.tsx`; the answer needs a loopback `Origin`).

## Claude panel (D92, A39)

- `claude_panel.py` writes the per-investigation working folder in the config folder:
  - `chembook3d-mcp.json` with only the chembook3d MCP server of D91 when the app has it;
  - `chembook3d-settings.json` pre-approving `mcp_tools.PANEL_ALLOWED` (its tools of kind "read", and `write_server_file`, whose go-ahead is a switch in the app, D126) and denying shell, editing and web tools;
  - a CLAUDE.md with the notebook's rules.
- It runs only `claude` in a pseudo-terminal (`pty` on Linux, pywinpty on Windows).
- `api/claude.py` hands out one-time tokens and serves the terminal WebSocket, refusing anything not local (bind address, client, Host, Origin).
- `components/ClaudePanel.tsx` is the xterm.js panel, the Claude tab of the side panel (`SidePanel.tsx`) docked right in `.app-body`, kept mounted while hidden.
- Tests use `tests/fake_claude.py` through `CHEMBOOK3D_CLAUDE`.

## Claude's structure tools (D144, A89)

- `POST /api/builder/run` (MCP read tool `build_structure`) runs the window's tools as `StepIn` steps through the same `_apply` as `/edit`, on atoms from 1 (`_from_one`), from a node (`_start` opens its own read-only session), XYZ text or nothing.
- `POST /api/builder/check` (`check_structure`) and every run's answer carry `_report`: formula, `electrons_note`, measurements, each metal's bonded atoms (elements not in `minimize.TYPED`) and `_contacts`.
- Saving stays `set_coordinates`; the server's `INSTRUCTIONS` and `claude_panel.NOTEBOOK_GUIDE` say to use them.
- D148: each run is kept by its `build_id` (`api/builds.py`, `app.state.builds`, the last `MAX_BUILDS` in memory), which `StartIn`, `GeometryIn` and `NodeIn` take instead of XYZ. `builder.open_sites` picks a metal's open site; `add_molecule` binds `donors` (`DONORS`) at `distance` (`_bound`); `add_hydrogens` takes `count`; `/run`'s `_warnings` lists unasked bonds and contacts per step. `list_nodes` takes `include_xyz` (off for Claude through `ToolSpec.defaults`).
- D149: room is distance over van der Waals radii (`builder._vdw`). `_site_fit` tries every open direction (`_site_directions`, `_fits`) and refuses below `TOO_TIGHT`; `_placed` keeps a new atom off its partner's bonds. Minimize holds a placed metal bond (`minimize._placed_bond`) with loose angles, straight angles by `_angle_held`, changed atoms first. `api/builder._trusted` drops implausible drawn bonds so `_close` reports clashes. `mcp_tools._not_null` writes optional fields as their type. `list_nodes` takes `brief` (on for Claude); `mcp_server._json_text` compacts long answers.
- D150: `builder.rotate_fragment` and `align_vectors` turn the atoms given rigidly about an axis of two points (an atom or atoms whose middle counts; default the set's binding axis, `_rigid_axis`), whatever the bonds, naming the bonds to the rest before and after (`_kept_bonds`). Steps `rotate_fragment` and `align_vectors` with `axis`, `vector` and `reference` (`api/builder._points`), Claude's only.
- Flip (D166) and measurements (D151, `measure_nodes`): see "Edit structure window".

## Git sync (D71)

- `sync.py` runs the installed `git`: link, pull on open, push on close or Sync, keep one copy on a conflict, clone. The app stores no credentials.
- Routes in `api/routes.py`. `components/SyncDialogs.tsx` holds the dialogs.
- Ownership during pull, push, link and resolve: see "Notebook identity and ownership".
- `tests/test_sync.py` uses a local bare repository as the remote.

## Read-only copy to share (D79, A30)

- `api/snapshot.py` (`POST /api/snapshot`) computes everything the reader can choose (energies per level and type, profiles and tables per reference on the exported pathways) with the API's own functions, drops local paths, copied files and history, and writes it as gzip+base64 JSON into the viewer page `frontend/dist-snapshot/snapshot.html`.
- That page is a second Vite build (`vite.snapshot.config.ts`, entry `snapshot.html` → `src/snapshot/`) with every script and style inlined, so it opens from disk.
- It reuses the canvas (`readOnly`), `ProfileChart`, `Viewer3D` and `OverviewSections`, with its own read-only parts: `snapshot/SnapshotApp.tsx` (the page), `snapshot/Panels.tsx` (the side panel, as text with nothing that edits) and `snapshot/Drawer.tsx` (`SnapshotDrawer`, with `EnergyTableView`).
- Helpers both builds share are in `src/util.ts`. Both drawers take a pathway's colour and legend name from `profileStyle.ts` (`pathwayLabels`, `pathwayLegend`).
- It opens on the view the app showed (D128, A73): the export sends `view` (D105's view state plus the node `mode`), `snapshot._view` cleans it with `view_state.clean`/`current` (none sent: `view_state.load`), and the copy carries it with a random `copy_id`. `snapshot/data.ts` `openingView` starts from it, then from the reader's own choices kept in localStorage per `copy_id` (`rememberView`).

## Starting and stopping (D107, A54)

- `cli.py` is `chembook3d`: `chembook3d mcp` starts the MCP server (D91), `chembook3d shortcut` makes the shortcut, `--no-browser` opens no tab, and `--launched` (set by the launcher) stops the server when the last tab closes.
- `launcher.py` is what the shortcut runs, by its path with the base Python (`python -I`, standard library only, so a pull may replace it):
  - already running → only a tab;
  - its own page on port 8764 shows the steps;
  - `git pull --ff-only` on a clean `main` only;
  - rebuild when `git rev-parse HEAD:frontend` differs from `frontend/dist/.chembook3d-build` (written by `scripts/build_frontend.py`; a failed rebuild puts the old build back);
  - then `uv run chembook3d --launched` with no window, waiting for it;
  - everything in `launcher.log` in the config folder.
- `shortcut.py` is `chembook3d shortcut` (Windows .lnk through PowerShell's WScript.Shell, Linux .desktop, WSL a .lnk to a hidden VBScript running `wsl.exe`) and saves uv's path and the tools' folders in `launcher.json`.
- `api/lifetime.py`: tabs long-poll `GET /api/presence` and send a goodbye beacon on `pagehide`.
  - Every request with a known tab's `X-Chembook-Client` counts as a word from it. A tab with a request still running is never dropped as silent (`TabRequests` middleware, `Lifetime.asking`/`answered`), since its long poll may wait behind it for one of the browser's six connections.
  - A `--launched` server stops `GRACE` s after the last goodbye or `SILENCE` s without a tab.
  - `POST /api/shutdown` (loopback Origin only, not an MCP tool) closes and pushes, then sets uvicorn's `should_exit`.
- Frontend: `lifecycle.ts` keeps the presence, and `components/ShutDown.tsx` holds the button, the launcher's notices and the page a stopped tab shows.

## Scripts

- `scripts/make_demo.py`: builds a small demo investigation (used by the UI tests too).
- `scripts/make_showcase_demo.py` builds the showcase demo (D170) from published ioChem-BD CML files, downloaded into a cache and checked against `scripts/showcase_manifest.json`. It drives the app's own HTTP API through a TestClient, so it shows what a user's clicks would do (tests in `tests/test_showcase_demo.py`).
- `scripts/build_frontend.py` runs `npm ci` and `npm run build` the same way on Windows, Linux and WSL, and refuses a Windows Node.js found through the PATH under WSL.

## Backend tests

- `tests/`: pytest. `tests/portability.py` is the cross-OS zip check CI runs (T-OPS-01).
- `tests/fixtures/` holds public sample outputs: `gaussian/` from GoodVibes, `orca/`, `xtb/`, `crest/`, `iochem/` (ioChem-BD CML) and `sterics/` (SambVca reference set). Sources and licences are in `tests/fixtures/README.md`.
- `tests/gaussian_text.py` writes small synthetic outputs for cases the samples lack, including the custom-basis `--Link1--` chains (`python -m tests.gaussian_text <folder>` writes them for the UI tests).
- Notebook identity and ownership tests: see that section.

## UI tests

- `frontend/e2e/`: Playwright UI tests against the real backend, one spec per feature (for example `e2e/refetch.spec.ts`, `e2e/local-jobs.spec.ts`, `frontend/e2e/equilibrium.spec.ts`).
  - `playwright.config.ts` starts the backend (:8766) and the fake SSH server (:8767) and runs one test at a time (`workers: 1`: the backend has one open investigation). Everything goes to a fresh temporary folder (`E2E_DIR`), app settings included.
  - `e2e/global-setup.ts` builds the demo once into `E2E_DIR/demo-template` (`scripts/make_demo.py`) and writes the synthetic Gaussian files.
  - `e2e/helpers.ts` holds what the specs share. `openDemo` opens the test's own copy of the demo (`demoFolder`: copied from the template on first use, without the lock and owner files, under `E2E_DIR/demo-copies/`), so a test may change the demo freely and needs no clean-up; the same test opening it again gets the same copy. Also `browseTo` and `openFolder` (the folder dialog, waiting for the listing), `newInvestigation`, `startPage`, `investigationCommand`, `canvasNode`, `pressInView` and `bringIntoView` (fit the canvas before clicking, see #188), `dragBy`, `deferred` and `pageSettled`.
  - No fixed waits: hold an answer back with `deferred` and a route, wait for the response (`page.waitForResponse`, then `response.finished()`) or the request that follows it, and use `pageSettled` before asserting that something did not happen. App settings are shared by all tests: a test that changes one puts it back in a `finally`.
- `frontend/tests/`: Node unit tests (`npm run test:unit`, Node 24+), such as `frontend/tests/api-identity.test.mjs`.
- The identity specs (`energy-request.spec.ts`, `investigation-identity.spec.ts`): see "Notebook identity and ownership".
