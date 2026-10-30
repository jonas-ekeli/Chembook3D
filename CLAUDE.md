# Chembook3D: notes for coding agents

This file is loaded into every session, so keep it short. Detail belongs in `docs/code-map.md` or the spec.

## Read first
- The specification is in `docs/spec/`. Start at `docs/spec/README.md`.
- `docs/spec/08-decisions-and-open-questions.md` is the source of truth. Cite decision IDs (Dnn, Pnn) in code comments or PRs where a rule comes from the spec, and never implement against an open question (Qnn) without asking Jonas.
- Build in the phase order of `docs/spec/09-implementation-roadmap.md`. Requirement IDs (FR-…) and test IDs (T-…) there map to `04-functional-requirements.md` and `07-validation-and-test-plan.md`.
- `docs/reference/thermochem_corr_G16.py` is Jonas's script, kept verbatim as the oracle for quasi-harmonic G (D56–D58). Do not reformat or "fix" it.

## Looking things up without reading whole files
- 08 (~80k tokens), 07 (~35k) and 04 (~18k) are too large to read whole. Every ID is the first cell of a table row, so look it up by ID: `grep -n "^| D150 " docs/spec/08-decisions-and-open-questions.md` (also `| A12 `, `| Q34 `, `| P7 `), `grep -n "^| FR-EN-03 " docs/spec/04-functional-requirements.md`, `grep -n "^| T-EN-01 " docs/spec/07-validation-and-test-plan.md`. For a topic, `grep -n -i` a word, then read only around the hit (Read with offset and limit). Read a whole spec file only when the task needs all of it.
- Highest number used so far: `grep -oE "^\| D[0-9]+" docs/spec/08-decisions-and-open-questions.md | tr -dc "0-9\n" | sort -n | tail -1` (A, Q and the FR-/T- series the same way). Open pull requests may hold the next ones, so check them too.
- `docs/code-map.md` says where each feature lives, one section per feature: `grep -n "^## " docs/code-map.md` lists them; read only the sections the task touches. When a change adds, renames or moves something a section names, update that section in the same PR.
- Large source files (`frontend/src/App.tsx`, `frontend/src/api.ts`, `services/imports.py`, `api/routes.py` and others over 1,000 lines): grep for the symbol and read the part around it. Hand broad reading across many files to a sub-agent and keep only its conclusion.

## Layout
- `src/chembook3d/`: Python backend (FastAPI). All domain rules (identity ID-*, invariants INV-*, energy rules EN-*) live here, not in the UI. `models.py` holds the SQLAlchemy records, `migrations/` the Alembic revisions, `services/` the domain rules, `api/` the HTTP routes, `parsers/` the output parsers (our own code, not cclib, D61, D64), `mcp_server.py` and `mcp_tools.py` the `chembook3d mcp` server.
- `frontend/`: TypeScript + React UI built with Vite (`src/App.tsx`, `src/components/`, `src/snapshot/` for the read-only copy). The backend serves `frontend/dist` when it exists.
- `tests/`: pytest, with public sample outputs in `tests/fixtures/`. `frontend/e2e/`: Playwright UI tests against the real backend. `scripts/`: demo data and the frontend build.

## Rules for every change
- Never add Jonas's own research outputs: the repository is public. Fixtures are public samples, with sources and licences in `tests/fixtures/README.md`; `tests/gaussian_text.py` writes synthetic outputs for cases the samples lack.
- Colours (D174): every colour is a token on `:root` in `frontend/src/index.css`, named for its use; add or reuse a token, never a colour literal. Buttons, fields and icons follow the system described under "Look of the interface" in `docs/code-map.md`.
- A new HTTP route needs an MCP tool in `mcp_tools.py` or an entry in `tests/test_mcp.py`'s NOT_OFFERED.
- A change to the order `imports.commit` writes in must keep `tests/test_import_undo.py` green.
- Download file names built from the user's text go through `api/downloads.py` and its mirror `fileName` in `frontend/src/util.ts`.
- Imported files are copied to `files/<source-file-id>/<name>`; the stored path uses `/`.

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
