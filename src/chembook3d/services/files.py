"""Copied source files (D16, FR-FILE-01…04). The copy inside the investigation is what the
app uses; the original path is information only and need not exist (FR-FILE-04)."""

import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

from chembook3d.models import SourceFile


def absolute_path(folder: Path, source: SourceFile) -> Path:
    """Stored paths use '/' so they work on Windows and Linux (NFR-PORT-01)."""
    return folder.joinpath(*PurePosixPath(source.stored_path).parts)


def launch(path: Path, reveal: bool = False) -> None:
    """Open a file with its default program, or show it in the file manager."""
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])
