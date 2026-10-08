"""Command-line entry point: `chembook3d` starts the local server and opens the browser;
`chembook3d mcp` runs the MCP server through which Claude works in it (D91);
`chembook3d shortcut` creates the shortcut that starts it without a terminal (D107)."""

import argparse
import platform
import shutil
import subprocess
import sys
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
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["mcp"]:  # D91: the MCP server for Claude, beside the running app
        from chembook3d.mcp_server import main as mcp_main

        mcp_main(argv[1:])
        return
    if argv[:1] == ["shortcut"]:  # D107: the one-click launcher's shortcut
        from chembook3d.shortcut import main as shortcut_main

        shortcut_main(argv[1:])
        return
    parser = argparse.ArgumentParser(prog="chembook3d", description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    # D107: started by the launcher, which has opened the tab already; the server then stops
    # when the last tab closes, and the tabs show the launcher's notices.
    parser.add_argument("--launched", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--notice", action="append", default=[], help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    url = f"http://{HOST}:{args.port}/"
    if args.launched:
        print(f"Chembook3D is running at {url}, started by the launcher", flush=True)
    else:
        # Without a desktop browser no tab opens; the address is then opened by hand.
        print(f"Chembook3D is running at {url} (press Ctrl+C to stop)", flush=True)
    if static_dir() is None:
        print(
            "The interface has not been built. Stop the server and run: "
            "uv run python scripts/build_frontend.py",
            flush=True,
        )
    if not (args.no_browser or args.launched):
        threading.Timer(1.0, open_browser, args=(url,)).start()
    app = create_app(launched=args.launched, notices=args.notice)
    # Open long polls and terminals would otherwise hold up a shut-down.
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=args.port, timeout_graceful_shutdown=5)
    )
    # Shut down in the app (D107) stops the server as Ctrl+C does.
    app.state.lifetime.exit = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
