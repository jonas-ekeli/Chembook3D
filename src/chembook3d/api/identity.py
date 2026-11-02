"""Bind a browser request to the investigation that its tab displays."""

from typing import Any
from urllib.parse import unquote

from fastapi import HTTPException, Request

HEADER = "x-chembook-investigation"
_CURRENT = object()


def check_investigation_identity(request: Request, investigation: Any = _CURRENT) -> None:
    """Legacy/MCP callers omit the header; tabs send a URI-encoded folder or an empty value.

    Call again when a route captures the active investigation, so a request waiting for a
    worker cannot silently acquire the notebook another tab opened in the meantime.
    """
    expected = request.headers.get(HEADER)
    if expected is None:
        # Starlette already decoded the query; decode only header values to preserve literal %.
        expected = request.query_params.get("expected_investigation")
    else:
        expected = unquote(expected)
    if expected is None:
        return
    if investigation is _CURRENT:
        investigation = getattr(request.app.state, "investigation", None)
    actual = "" if investigation is None else str(investigation.folder)
    if expected != actual:
        raise HTTPException(409, "The investigation changed; wait for this tab to update")
