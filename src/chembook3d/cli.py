"""Command-line entry point: `chembook3d` starts the local server and opens the browser."""

import argparse
import platform
import shutil
import subprocess
import threading
import webbrowser
from pathlib import Path

import uvicorn

from chembook3d.app import create_app, static_dir

HOST = "127.0.0.1"  # loopback only (NFR-SEC-01)


def running_in_wsl() -> bool:
    if platform.system() != "Linux":
        return False
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def open_browser(url: str) -> None:
    """Open the app in a browser tab. Under WSL that is the Windows default browser: the Linux
    side usually has none, and xdg-open only prints an error there."""
    if running_in_wsl():
        explorer = shutil.which("explorer.exe")
        if explorer is not None:
            # explorer.exe returns 1 even when it opened the page, so its result is not checked.
            subprocess.run([explorer, url], cwd="/", capture_output=True)
        return
    webbrowser.open(url)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chembook3d", description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = parser.parse_args(argv)

    url = f"http://{HOST}:{args.port}/"
    # Without a desktop browser no tab opens; the address is then opened by hand.
    print(f"Chembook3D is running at {url} (press Ctrl+C to stop)", flush=True)
    if static_dir() is None:
        print(
            "The interface has not been built. Stop the server and run: "
            "uv run python scripts/build_frontend.py",
            flush=True,
        )
    if not args.no_browser:
        threading.Timer(1.0, open_browser, args=(url,)).start()
    uvicorn.run(create_app(), host=HOST, port=args.port)


if __name__ == "__main__":
    main()
