# Chembook3D: notes for coding agents

## Read first
- The specification is in `docs/spec/`. Start at `docs/spec/README.md`.
- `docs/spec/08-decisions-and-open-questions.md` is the source of truth. Cite decision IDs (Dnn, Pnn) in code comments or PRs where a rule comes from the spec, and never implement against an open question (Qnn) without asking Jonas.
- Build in the phase order of `docs/spec/09-implementation-roadmap.md`. Requirement IDs (FR-…) and test IDs (T-…) there map to `04-functional-requirements.md` and `07-validation-and-test-plan.md`.
- `docs/reference/thermochem_corr_G16.py` is Jonas's script, kept verbatim as the oracle for quasi-harmonic G (D56–D58). Do not reformat or "fix" it.

## Layout
- `src/chembook3d/`: Python backend (FastAPI). All domain rules (identity ID-*, invariants INV-*, energy rules EN-*) live here, not in the UI.
- `frontend/`: TypeScript + React UI built with Vite. The backend serves `frontend/dist` when it exists.
- `src/chembook3d/investigation.py`: the investigation folder (database, `files/`, lock file, migrations with backup, clean-up of copies left by an interrupted import). `models.py` holds the SQLAlchemy records, `migrations/` the Alembic revisions, `services/` the domain rules, `api/routes.py` the HTTP API.
- `src/chembook3d/parsers/`: output parsers, one `ParsedStep` per job step (`common.py`), all our own code, not cclib (D61, D64): `gaussian.py`, `orca.py`, `xtb.py`; `crest.py` reads a conformer ensemble. `services/imports.py` detects which one a file is, stages it, plans which steps attach to which node (preview) and commits; `services/levels.py` holds levels of theory and the named custom basis sets and dispersions; `services/warnings.py` computes W-* warnings; `units.py` the energy conversion factors.
- Pathway structure (phase 3): `services/steps.py` (reaction steps), `services/branches.py` (branches, lineage, split), `services/transitions.py` (edges; "direct" means neither end is a TS, D53), `services/groups.py` (reconnect, add members, dissolve, delete with contents; members keep their branch, D66; a group also stores its member layout), `services/layout.py` (arrange a branch by step); their routes are in `api/pathway.py`.
- Free species (D69): a `Node` with `kind == "species"` is a substrate or fragment kept off the canvas; `TransitionSpecies` lists those that join or leave on a transition. `services/species.py` holds the rules (attach, kind changes, the balance added along a pathway, W-BALANCE); `pathways.profiles` and the energy view use it. `balances_from` gives each node's balance along the route from the reference (D72), for node cards (the energy view's `relative`) and for pathways that miss the reference.
- Energies (phase 4): `thermochem.py` computes quasi-harmonic G from stored frequency results (it mirrors the reference script; tests compare both), `services/energies.py` resolves E/H/G/G_qh at a composite level key `levelId~geometryLevelId` (EN-3, EN-4, EN-7), `services/pathways.py` builds pathways, profiles and the energy table (values pre-formatted, CSV uses the same strings, T-EN-08); routes in `api/energies.py`.
- Frontend: `App.tsx` holds the selection and the energy view (level, type, reference); `components/Canvas.tsx` is the React Flow canvas (its own copy of the nodes keeps measured sizes), `Outline.tsx` the steps, branches and search beside it, `NodeInspector.tsx` and `PathwayInspectors.tsx` the side panel, `Viewer3D.tsx` the view-only 3Dmol viewer. `EnergyDrawer.tsx` is the profile chart and energy table below the canvas. `chem.ts` has xyz parsing, bonds and measurements.
- Resume overview (phase 5): `services/overview.py`, route `GET /api/overview` in `api/pathway.py`, shown by `components/Overview.tsx` whenever nothing is selected. A CREST ensemble imports as a group (`_plan_ensemble`, `_commit_ensemble` in `services/imports.py`).
- Imported files are copied to `files/<source-file-id>/<name>`; the stored path uses `/`.
- Git sync (D71): `sync.py` runs the installed `git` (link, pull on open, push on close or Sync, keep one copy on a conflict, clone); the app stores no credentials. Routes are in `api/routes.py` beside open and close; `components/SyncDialogs.tsx` holds the dialogs. `investigation.py` refuses a database from a newer app (`InvestigationTooNew`) and, on request, asks before upgrading (`NeedsUpgrade`). `tests/test_sync.py` uses a local bare repository as the remote.
- `tests/`: pytest. `tests/portability.py` is the cross-OS zip check CI runs (T-OPS-01). `tests/fixtures/` holds public sample outputs (`gaussian/` from GoodVibes, `orca/`, `xtb/`, `crest/`), with sources and licences in `tests/fixtures/README.md`; never add Jonas's own research outputs, the repository is public. `tests/gaussian_text.py` writes small synthetic outputs for cases the samples lack, including the custom-basis `--Link1--` chains (`python -m tests.gaussian_text <folder>` writes them for the UI tests).
- `frontend/e2e/`: Playwright UI tests against the real backend.
- `scripts/make_demo.py`: builds a small demo investigation (used by the UI tests too). `scripts/build_frontend.py` runs `npm ci` and `npm run build` the same way on Windows, Linux and WSL, and refuses a Windows Node.js found through the PATH under WSL.

## Commands
- Backend setup: `uv sync`
- Lint and format: `uv run ruff check .` and `uv run ruff format --check .`
- Tests: `uv run pytest -W error`
- Frontend: `cd frontend && npm ci && npm run lint && npm run build`
- UI tests: build the frontend, then `cd frontend && npm run test:e2e` (starts the backend on port 8766 itself). In a container with a preinstalled Chromium, set `PLAYWRIGHT_CHROMIUM_PATH` to it instead of running `playwright install`.
- Schema changes: add an Alembic revision under `src/chembook3d/migrations/versions/`; opening an older investigation backs up its database, then migrates it.
- Demo data: `uv run python scripts/make_demo.py <new-folder>`
- Run the app: build the frontend once (`uv run python scripts/build_frontend.py`), then `uv run chembook3d` (opens the browser at http://127.0.0.1:8765). For UI development, run `uv run chembook3d --no-browser` and `npm run dev` in `frontend/`; Vite proxies `/api` to the backend.

## Rules
- The server binds to 127.0.0.1 only (NFR-SEC-01).
- Paths stored in an investigation are relative, so folders move between Windows and Linux (NFR-PORT-01).
- CI runs on Windows and Linux; keep both green.
- User-facing commands in README.md must work in Windows PowerShell 5.1 as well as bash (no `&&` chains, no `cd a && b`).
