"""FastAPI application: the local API plus the built web interface."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from chembook3d import __version__, cloud_jobs
from chembook3d.api.batch_imports import router as batch_router
from chembook3d.api.claude import Panel
from chembook3d.api.claude import router as claude_router
from chembook3d.api.energies import router as energy_router
from chembook3d.api.import_undo import router as undo_router
from chembook3d.api.jobs import router as job_router
from chembook3d.api.lifetime import SHUTDOWN_PUSH_TIMEOUT, Lifetime, watch
from chembook3d.api.lifetime import router as lifetime_router
from chembook3d.api.live import ChangeTracker, LiveState
from chembook3d.api.live import router as live_router
from chembook3d.api.notes import router as note_router
from chembook3d.api.pathway import router as pathway_router
from chembook3d.api.routes import close_and_push, router
from chembook3d.api.selectivity import router as selectivity_router
from chembook3d.api.snapshot import router as snapshot_router
from chembook3d.api.sterics import router as steric_router
from chembook3d.api.turnover import router as turnover_router
from chembook3d.api.view_state import router as view_state_router
from chembook3d.services.imports import Staging
from chembook3d.services.records import RecordError, RecordNotFound


def static_dir() -> Path | None:
    """Locate the built frontend: packaged copy first, then the repo's frontend/dist."""
    packaged = Path(__file__).parent / "static"
    repo_build = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    for candidate in (packaged, repo_build):
        if (candidate / "index.html").is_file():
            return candidate
    return None


class InterfaceFiles(StaticFiles):
    """The built interface. Its HTML page names the current bundle, so the browser must check
    it on every load (no-cache: it still gets a quick "not modified" answer). Without this a
    browser could keep showing the old interface for hours after a rebuild."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.media_type == "text/html":
            response.headers["Cache-Control"] = "no-cache"
        return response


NOT_BUILT_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Chembook3D</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40em; margin: 4em auto">
<h1>The interface has not been built yet</h1>
<p>Stop the server (Ctrl+C), build the interface once, then start it again:</p>
<pre>uv run python scripts/build_frontend.py
uv run chembook3d</pre>
<p>See README.md for installing Node.js on Windows, Linux or WSL.</p>
</body></html>
"""


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.investigation = None
    app.state.staging = Staging()
    # D107: started by the launcher, the server stops when the last tab has closed.
    lifetime = app.state.lifetime
    watcher = asyncio.create_task(watch(lifetime)) if lifetime.launched else None
    yield
    if watcher is not None:
        watcher.cancel()
    app.state.claude_panel.close_all()  # stop Claude Code in any open panel (D92)
    app.state.cloud_jobs.close_all()  # and `claude --cloud` launchers; sessions go on (D93)
    app.state.staging.clear()  # previewed but not imported files (FR-IMP-05)
    if app.state.investigation is not None:  # release the lock file on shutdown (P22)
        # A linked investigation is also pushed, with a short time limit (FR-SYNC-05);
        # what does not get through is pushed when it is next opened (FR-SYNC-04).
        status = close_and_push(app.state.investigation, timeout=SHUTDOWN_PUSH_TIMEOUT)
        if status is not None:
            print(f"Chembook3D sync: {status.message}", flush=True)


def create_app(launched: bool = False, notices: list[str] | None = None) -> FastAPI:
    """launched: started by the launcher (D107), which passes what tabs should be told."""
    app = FastAPI(title="Chembook3D", version=__version__, lifespan=_lifespan)
    app.state.lifetime = Lifetime(launched=launched, notices=list(notices or []))
    app.state.investigation = None
    app.state.staging = Staging()
    app.state.claude_panel = Panel()
    app.state.cloud_jobs = cloud_jobs.Launcher()  # D93
    app.state.live = LiveState()  # D91: changes, selection and confirmations
    app.add_middleware(ChangeTracker, live=app.state.live)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.exception_handler(RecordError)
    def record_error(_request: Request, exc: RecordError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RecordNotFound)
    def record_not_found(_request: Request, exc: RecordNotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    app.include_router(router)
    app.include_router(batch_router)
    app.include_router(undo_router)
    app.include_router(pathway_router)
    app.include_router(energy_router)
    app.include_router(snapshot_router)
    app.include_router(steric_router)
    app.include_router(selectivity_router)
    app.include_router(note_router)
    app.include_router(turnover_router)
    app.include_router(claude_router)
    app.include_router(job_router)
    app.include_router(view_state_router)  # D105
    app.include_router(live_router)
    app.include_router(lifetime_router)  # D107

    static = static_dir()
    if static is not None:
        app.mount("/", InterfaceFiles(directory=static, html=True), name="ui")
    else:

        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            return HTMLResponse(NOT_BUILT_PAGE, status_code=503)

    return app
