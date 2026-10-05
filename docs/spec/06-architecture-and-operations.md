# 06 · Architecture and operations

_Chembook3D specification pack · v1.1 · 2026-09-29. Confirmed items are marked with their decision ID. Proposals P16–P30 were confirmed by Jonas on 2026-09-29 (D51); P23 is amended by D55 (uv)._

## 1. Confirmed constraints

| ID | Constraint | Decision |
|---|---|---|
| C1 | Runs locally, for a single user with no login | D42 |
| C2 | Windows and Linux | D10 |
| C3 | Python backend | D22 |
| C4 | Direction B: Python backend (FastAPI, SQLite) with a TypeScript web interface (React Flow canvas, 3Dmol.js viewer) | D38 |
| C5 | One self-contained folder per investigation; backup is the user's job, optionally through Git sync (D71) | D21, D71 |
| C6 | No cloud services required (Git sync is optional, D71); hosting kept possible for later | D42, D71 |
| C7 | Never launches or monitors calculations | D9 |

## 2. Component view

```
┌────────────── Browser or desktop window (Q33) ──────────────┐
│ TypeScript + React UI                                        │
│  • Canvas (React Flow)  • 3D (3Dmol.js)  • Profile/table     │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP/JSON on 127.0.0.1 only
┌───────────────────────────▼─────────────────────────────────┐
│ Python backend (FastAPI)                                     │
│  • API layer            • Domain services (rules in 02)       │
│  • Parsers (05 §3)      • Thermochemistry (05 §4)             │
│  • History writer       • File store                          │
└───────────────────────────┬─────────────────────────────────┘
                            │
      Investigation folder: investigation.sqlite + files/
```

- **Domain rules live in the backend.** Identity rules (ID-*), invariants (INV-*) and energy rules (EN-*) are enforced by backend services, not only by the UI. That keeps the rules testable in Python, and keeps them in place if the UI or a future hosted mode changes.
- **The API is local-only:** it binds to 127.0.0.1 (NFR-SEC-01).
- **Claude works through the same API (D91).** `chembook3d mcp`, started by Claude Code or Claude Desktop on the same computer, turns Claude's tool calls into API requests to the running app, so the rules above hold for it too. It never opens the database. Open tabs wait on `GET /api/live` and reload when something is changed elsewhere; deletes Claude asks for wait there for the user's Confirm, and only the app's page can answer them.
- **Calculations go to Claude Code cloud sessions (D93).** `cloud_jobs.py` writes job folders in `jobs/`, commits and pushes only those (and the cloud files in `.claude/`) to the investigation's GitHub repository, and runs the person's own `claude --cloud`; the session runs xTB or CREST and pushes outputs to a branch of its own, which the app copies back from. The database never goes through this path, so nothing is merged.
- The backend serves the built UI as static files, so running one command starts everything (P19).

## 3. Storage

| Item | Decision |
|---|---|
| Database | One SQLite file per investigation, inside its folder (C4, C5). P20: SQLite with foreign keys on and WAL mode off, so the folder is a single consistent unit when zipped or synced. |
| Schema access | P21: SQLAlchemy 2.x with Alembic migrations, and a schema version stored in the database. Opening an older investigation migrates it after making a backup copy of the database file. |
| Files | `files/<calculation-id>/<original-name>`, with relative paths only (FR-INV-03). |
| Settings | App-wide settings (unit, N, tolerance, device name) in a user config file. Investigation-specific values in the database. |

**Risk:** SQLite files in cloud-synced folders (Dropbox, OneDrive) can be corrupted if two machines open the investigation at once. Mitigation (P22): a lock file in the folder while it is open, with a warning when opening an investigation that is locked elsewhere.

**Git sync (D71):** a linked investigation is a Git repository whose remote is a private repository. The app calls the installed `git` (`src/chembook3d/sync.py`); pushing an open investigation first takes SQLite's write lock so no transaction is half-written in the commit. The database is never merged. Git is needed only for linked investigations.

## 4. Packaging and deployment

| Item | Status |
|---|---|
| Install and environment | **P23 as amended by D55:** uv manages the Python environment, dependencies (lock file) and install. The package includes the pre-built UI. `chembook3d` starts the backend and opens the default browser. A native-window wrapper (pywebview) is optional (Q33). |
| Python version | 3.11+ (P24), pinned through uv. |
| Node.js | Needed only to build the UI, not to run it. |
| Hosting later | Not designed now (X4). Because the backend is already an HTTP API, a later hosted mode would add login, access control, multi-user storage and server-side file storage. These are future decisions. |

## 5. Operational and quality requirements (NFR)

| ID | Requirement | Trace |
|---|---|---|
| NFR-PORT-01 | An investigation created on Windows opens on Linux and vice versa (paths stored relative, UTF-8, `/` separators). | D10, D21 |
| NFR-PORT-02 | The app installs and runs on current Windows 10/11 and a mainstream Linux distribution without admin rights (P25). | D10 |
| NFR-DATA-01 | No write leaves the database inconsistent: each user action runs in one transaction. | D21 |
| NFR-DATA-02 | Copied source files are never modified after import. | D16 |
| NFR-DATA-03 | Before a schema migration, the database file is backed up inside the folder. | P21 |
| NFR-DATA-04 | Crashes or kills during import leave no partial records. Orphaned copied files are cleaned up on next open. | P26 |
| NFR-SEC-01 | The backend listens on the loopback interface only and has no remote access. | D42 |
| NFR-SEC-02 | Imported files are parsed as data only. Nothing in them is executed. | — |
| NFR-SEC-03 | The app stores no passwords or tokens. Git sync and cloud jobs sign in through the credential helper of the `git` and the sign-in of the `claude` installed on the computer, never through the app. | D71, D93 |
| NFR-PERF-01 | **No numerical performance targets are set.** Jonas has not confirmed any. The implementer should report load and render times on the reference example and on a stress case (see [07](07-validation-and-test-plan.md)) so targets can be set later. | HANDOFF |
| NFR-UX-01 | Every destructive action (delete node, remove group member, delete step) asks for confirmation and lists what is affected. | P3 |
| NFR-LIC-01 | Third-party libraries must allow use and redistribution in a tool Jonas may share (P27: permissive licences preferred; React Flow MIT, 3Dmol.js BSD, cclib BSD, FastAPI MIT, morfeus MIT with SciPy BSD, D81). | D6 |

## 6. Decisions needed before implementation

| Needed before | Decision | Recommendation |
|---|---|---|
| Phase 1 | Q33: browser tab or native window | Browser tab first; add pywebview later if wanted |
| Phase 2 | P16: cclib as the base parser | Try it on Jonas's sample outputs first |
| Phase 4 | Reference script and outputs (R1) | Oracle for the G_qh tests |
