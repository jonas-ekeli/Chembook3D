"""D226: figures and read-only copies Claude makes for the user are files in the
investigation's `exports` folder, since Claude has no browser download to hand them over with.
A file there is never replaced: a second one of the same name gets a number."""

from __future__ import annotations

from pathlib import Path

from chembook3d.api import downloads

EXPORT_DIR = "exports"
KINDS = (".png", ".svg", ".html")
LARGEST = 64 * 1024 * 1024  # bytes; a canvas image at twice its size stays far below


class ExportError(ValueError):
    pass


def save(folder: Path, name: str, content: bytes) -> str:
    """Write `content` as a new file named after `name`; answers with its path in the
    investigation, with `/` (NFR-PORT-01)."""
    stem, dot, kind = name.rpartition(".")
    kind = (dot + kind).lower()
    if not stem or kind not in KINDS:
        raise ExportError(f"An export is one of {', '.join(KINDS)}")
    if not content:
        raise ExportError("The export is empty")
    if len(content) > LARGEST:
        raise ExportError("The export is too large to save")
    stem = downloads.file_name(stem, "export")
    target = folder / EXPORT_DIR
    target.mkdir(exist_ok=True)
    number = 1
    while True:
        path = target / (f"{stem}{kind}" if number == 1 else f"{stem} {number}{kind}")
        try:
            with path.open("xb") as handle:
                handle.write(content)
        except FileExistsError:
            number += 1
            continue
        return f"{EXPORT_DIR}/{path.name}"
