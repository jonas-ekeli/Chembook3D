# Architecture map

A short map of Chembook3D for developers and coding agents: what runs, where each layer lives,
and how an output file becomes an energy. Read this first; then `grep -n "^## " docs/code-map.md`
for the feature you need. Paths are relative to the repository root; the backend is under
`src/chembook3d/` (written `cb/` below), the interface under `frontend/src/` (`fe/`).

## Processes

```mermaid
flowchart LR
    UI["fe/ React + TS (Vite build in frontend/dist)<br/>App.tsx · components/Canvas.tsx (React Flow)<br/>Viewer3D.tsx (3Dmol.js) · EnergyDrawer.tsx"]
    API["cb/app.py create_app · cb/cli.py (uvicorn 127.0.0.1:8765)<br/>routes: cb/api/*.py · rules: cb/services/*.py"]
    DB[("investigation.sqlite + files/{id}/{name}<br/>cb/models.py · cb/migrations/versions/")]
    MCP["cb/mcp_server.py + cb/mcp_tools.py<br/>(chembook3d mcp, stdio)"]
    CP["cb/claude_panel.py · cb/api/claude.py<br/>(claude CLI in a pty)"]
    KEEP["cb/remote/keeper.py (asyncssh, own process)<br/>cb/remote/client.py · cb/api/remote.py"]
    GIT["cb/sync.py (git subprocess)<br/>cb/cloud_jobs.py (job folder, claude --cloud)"]
    LOC["cb/local_jobs.py → cb/local_worker.py<br/>(detached; tools env installed by cb/local_tools.py)"]
    UI <-->|"fetch fe/api.ts · long poll fe/live.ts (/api/live)"| API
    API <--> DB
    CP --> MCP -->|"HTTP + X-Chembook-Client: claude"| API
    API <-->|"JSON lines on 127.0.0.1"| KEEP
    API --> GIT
    API --> LOC
```

- WebSockets only for terminals: `/api/claude/terminal`, `/api/remote/terminal`, and
  `/api/claude/launch-view` (the screen of a `claude --cloud` launch, `fe/components/LaunchQuestion.tsx`).
- `cb/shortcut.py` writes the desktop shortcut (`chembook3d shortcut`); `cb/launcher.py` runs it: progress page on :8764, `git pull`, rebuild if
  `frontend/` changed, then starts `chembook3d --launched`; `cb/api/lifetime.py` stops a launched
  server after the last tab closes.
- Cloud jobs: `cb/cloud_jobs.py` commits a job folder to the investigation's GitHub repository
  and starts a Claude Code cloud session; `cb/cloud_templates.py` writes its setup files and
  `cb/pathtools.py` is copied in for scan-path jobs. Results come back through sync.
- SSH: `cb/remote/servers.py` keeps the cluster list (`servers.json` in the config dir).
- `cb/api/snapshot.py` + `fe/snapshot/`: the read-only HTML copy (second Vite build,
  `frontend/dist-snapshot/`).

## Layers

| Layer | Where | Notes |
|---|---|---|
| CLI and app | `cb/cli.py`, `cb/app.py`, `cb/settings.py` | App settings and tolerances in `settings.py`; config dir from platformdirs or `CHEMBOOK3D_CONFIG_DIR`. Serves a packaged `cb/static/` if present, else `frontend/dist`. |
| Investigation folder | `cb/investigation.py` | Open/create, lock (`.chembook3d.lock`, `.chembook3d.owner`), back up then Alembic-migrate. |
| Records | `cb/models.py` | InvestigationInfo (T, cutoff, standard state, repairs), Node, Calculation, CalculationResult (1:1), LevelOfTheory, CustomBasis, CustomDispersion, SourceFile, ReactionStep, Branch, GroupNode, Transition, TransitionSpecies, SubInvestigation, NodeNote, NoteImage, HistoryEntry; analyses AlignmentSet, StericProfile, Selectivity (+Outcome, Member), EnergyProfile, Turnover, ConformerEquilibrium. |
| HTTP routes | `cb/api/` | One file per area: `routes.py` (nodes, calculations, imports) and `pathway.py` (steps, branches, transitions, groups) are the largest; the rest are named for their feature (`energies.py`, `energy_profiles.py`, `selectivity.py`, `turnover.py`, `equilibrium.py`, `sterics.py`, `irc.py`, `scan_paths.py`, `scan_editor.py`, `inspect.py`, `builder.py`, `local.py`, `remote.py`, `claude.py`, `jobs.py`, …). Helpers without routes: `identity.py` (binds a request to its tab's investigation), `downloads.py`, `builds.py`. A new route needs an MCP tool or a NOT_OFFERED entry in `tests/test_mcp.py`. |
| Domain rules | `cb/services/` | All rules (ID-*, INV-*, EN-*) live here, never in `fe/`. |
| Parsers | `cb/parsers/` | Own code, not cclib: `gaussian.py`, `orca.py`, `xtb.py`, `crest.py`, `cml.py`, `joined.py`, `local_result.py`; common types in `common.py`. |
| Thermochemistry | `cb/thermochem.py`, `cb/units.py` | Truhlar G_qh (D56–D58); CODATA 2018 constants. Oracle: `docs/reference/thermochem_corr_G16.py`. |
| History | `cb/services/history.py` | Append-only `history` table; `source` = manual, import or claude. |

## Import → energy pipeline

| Step | Code | Rule |
|---|---|---|
| 1. Stage | `services/imports.py` `Staging.add` | Temp copy + SHA-256; nothing in the database. |
| 2. Detect | `imports.read_file` | By content: local result, joined scan, xTB structure trajectory (`xtbscan.log`), Gaussian, CML, ORCA, xTB, CREST. |
| 3. Parse | `parsers/*` → `ParsedFile(program, version, steps)` (CREST → `crest.Ensemble`) | Correlated methods take their own total (D153); missing values → W-PARSE. |
| 4. Plan / preview | `imports.plan`, `services/levels.py`, `services/geometry.py` | Level key = program, method, basis, dispersion, solvation model, solvent. Attach if Kabsch RMSD ≤ 0.001 Å; W-DUP ≤ 0.05 Å (D62; defaults in `settings.py`). |
| 5. Commit | `imports.commit` | One transaction, fixed write order (keep `tests/test_import_undo.py` green); file to `files/<source-file-id>/<name>`. |
| 6. Batch / undo | `services/batch_import.py`, `services/import_undo.py` | Undo replays the history in reverse. |
| 7. Energies | `services/energies.py` (`LevelKey`, `NodeEnergies`) | Composite level `levelId~geometryLevelId` (D31); same level only, no fallback (D27); other charge/multiplicity left out (D155). |
| 8. Thermo | `thermochem.py`, `services/thermo_settings.py`, `services/thermo_model.py`, `services/concentrations.py` | G_qh per investigation T/cutoff (D159); 1 M option (D95, D160). |
| 9. Checks | `services/warnings.py`, `services/spin.py`, `services/energies.py` | W-* codes travel in each value's `check`; they never change a value or a status (D154, D19). |
| 10. Analyses | `services/pathways.py`, `turnover.py`, `selectivity.py`, `equilibrium.py`, `group_populations.py` | Profiles, energetic span, selectivity, populations. |

## Tests

`tests/` (pytest, fixtures in `tests/fixtures/` with sources in its README), `frontend/tests/`
(Node unit tests, `npm run test:unit`), `frontend/e2e/` (Playwright against the real backend on
:8766, with a fake SSH server on :8767). The frontend unit tests import the pure `.ts` modules
directly (Node strips the types) and cover the API client, energy units and corrections, XYZ
parsing and measuring, Tidy, edge label placement, the profile axis, the read-only copy's data and
file names; CI runs them as `npm run test:coverage`, which prints Node's coverage of `src/`.
`tests/fixtures/file-names.json` holds download file name examples that both the backend's
`api/downloads.py` and the frontend's `fileName` are tested on. CI (`.github/workflows/ci.yml`, Ubuntu + Windows): Backend,
Frontend, UI tests, Portability (`tests/portability.py` makes an investigation on one OS and
opens it on the other), and Local tools (installs the real tools and runs `tests/test_local_*.py`).
Backend runs pytest in parallel (`-n auto`, pytest-xdist) and lists the 25 slowest tests; a test
running over 2 minutes prints its traceback (`faulthandler_timeout`). Every job has a time limit,
a newer push to a pull request cancels the older run, and actions are pinned to commit SHAs
(Dependabot proposes updates, `.github/dependabot.yml`).
