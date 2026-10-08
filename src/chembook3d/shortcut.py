"""`chembook3d shortcut` (D107, FR-RUN-01): the Chembook3D shortcut that runs the launcher.

- Windows: on the desktop and in the Start menu, running `launcher.py` with a windowless Python.
- Linux: an entry in the app menu (`~/.local/share/applications`), with no terminal.
- WSL: a Windows shortcut on the desktop and in the Start menu, whose small script starts the
  launcher inside WSL with its console window hidden.

It also saves, in the app's config folder, where uv is and the folders of the tools the app
uses (git, Node.js, Claude Code), because a shortcut does not get the terminal's PATH. Running
it again repairs the shortcut, for example after uv, Node.js or the checkout moved.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from chembook3d import launcher
from chembook3d.cli import running_in_wsl

NAME = "Chembook3D"
DESCRIPTION = "Notebook for computational chemistry mechanism investigations"
ASSETS = Path(__file__).parent / "assets"
TOOLS = ("uv", "git", "node", "npm", "claude")  # whose folders the launcher puts on the PATH


class ShortcutError(Exception):
    pass


def find_uv() -> str | None:
    found = os.environ.get("UV") or shutil.which("uv")  # `uv run` names itself in UV
    return found if found and Path(found).is_file() else None


def base_python(windowless: bool = False) -> Path:
    """The Python the launcher runs with: the one uv made the environment from. The
    environment's own may be replaced by the `uv sync` the launcher starts, and the launcher
    needs only the standard library. On Windows the windowless pythonw.exe beside it."""
    base = Path(getattr(sys, "_base_executable", None) or sys.executable)
    if windowless and sys.platform == "win32":
        pythonw = base.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return base


def save_settings(folder: Path, uv: str, browser: str | None = None) -> Path:
    paths: list[str] = []
    for tool in TOOLS:
        found = shutil.which(tool)
        if found:
            paths.append(str(Path(found).parent))
    settings = {"uv": uv, "path": list(dict.fromkeys(paths)), "browser": browser}
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / launcher.SETTINGS
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path


# ---------- Windows (also from WSL) ----------


def ps_quote(text: str) -> str:
    """A PowerShell string literal."""
    return "'" + text.replace("'", "''") + "'"


def powershell(exe: str, script: str) -> str:
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        raise ShortcutError(f"PowerShell failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def windows_folders(exe: str) -> dict[str, str]:
    """The Windows desktop, Start menu programs and local app data folders (also when the
    desktop is moved to OneDrive)."""
    names = ("Desktop", "Programs", "LocalApplicationData", "System")
    script = "; ".join(f"[Environment]::GetFolderPath('{name}')" for name in names)
    lines = [line.strip() for line in powershell(exe, script).splitlines() if line.strip()]
    if len(lines) != len(names):
        raise ShortcutError("Windows did not name its desktop and Start menu folders")
    return dict(zip(names, lines, strict=True))


def write_lnk(exe: str, link: str, target: str, arguments: str, workdir: str, icon: str) -> None:
    """A Windows .lnk file, made through the WScript.Shell COM object (no extra package)."""
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(link)}); "
        f"$s.TargetPath = {ps_quote(target)}; $s.Arguments = {ps_quote(arguments)}; "
        f"$s.WorkingDirectory = {ps_quote(workdir)}; $s.IconLocation = {ps_quote(icon + ',0')}; "
        f"$s.Description = {ps_quote(DESCRIPTION)}; $s.Save()"
    )
    powershell(exe, script)


def windows_shortcuts(exe: str, target: str, arguments: str, workdir: str, icon: str) -> list[str]:
    folders = windows_folders(exe)
    made = []
    for key in ("Desktop", "Programs"):
        link = folders[key].rstrip("\\") + f"\\{NAME}.lnk"
        write_lnk(exe, link, target, arguments, workdir, icon)
        made.append(link)
    return made


def make_windows() -> list[str]:
    exe = shutil.which("powershell") or shutil.which("powershell.exe")
    if exe is None:
        raise ShortcutError("PowerShell was not found")
    python = base_python(windowless=True)
    # -I: the launcher's folder is not put on sys.path, so nothing there shadows the library.
    arguments = f'-I "{Path(launcher.__file__)}"'
    return windows_shortcuts(
        exe, str(python), arguments, str(launcher.REPO), str(ASSETS / "chembook3d.ico")
    )


def wsl_script(distro: str, python: str, script: str) -> str:
    """A VBScript that starts the launcher in WSL with wsl.exe's console window hidden."""

    def vbs(text: str) -> str:
        return text.replace('"', '""')

    command = f'wsl.exe -d "{distro}" -e "{python}" -I "{script}"'
    return (
        "' Chembook3D: starts the launcher inside WSL with no window (D107).\r\n"
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run "{vbs(command)}", 0, False\r\n'
    )


def make_wsl() -> tuple[list[str], str | None]:
    exe = shutil.which("powershell.exe")
    distro = os.environ.get("WSL_DISTRO_NAME")
    if exe is None or not distro:
        raise ShortcutError("Windows' PowerShell cannot be reached from WSL (is interop on?)")
    folders = windows_folders(exe)
    win_folder = folders["LocalApplicationData"].rstrip("\\") + "\\Chembook3D"
    converted = subprocess.run(["wslpath", "-u", win_folder], capture_output=True, text=True)
    if converted.returncode != 0:
        raise ShortcutError(f"wslpath could not convert {win_folder}")
    here = Path(converted.stdout.strip())
    here.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "chembook3d.ico", here / "chembook3d.ico")
    text = wsl_script(distro, str(base_python()), str(Path(launcher.__file__)))
    (here / "chembook3d-wsl.vbs").write_text(text, encoding="utf-16")  # with a BOM for wscript
    wscript = folders["System"].rstrip("\\") + "\\wscript.exe"
    made = windows_shortcuts(
        exe,
        wscript,
        f'"{win_folder}\\chembook3d-wsl.vbs"',
        win_folder,
        f"{win_folder}\\chembook3d.ico",
    )
    return made, shutil.which("explorer.exe")


# ---------- Linux ----------


def desktop_quote(arg: str) -> str:
    """One argument of a desktop entry's Exec key (Desktop Entry Specification)."""
    if arg and not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg.replace("%", "%%")
    inner = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    # The string escape (\\ for a backslash) applies before the quoting.
    return '"' + inner.replace("\\", "\\\\").replace("%", "%%") + '"'


def desktop_entry(python: str, script: str, repo: str, icon: str) -> str:
    command = " ".join(desktop_quote(a) for a in (python, "-I", script))
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.5\n"
        f"Name={NAME}\n"
        f"Comment={DESCRIPTION}\n"
        f"Exec={command}\n"
        f"Path={repo}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "StartupNotify=false\n"
        "Categories=Science;Chemistry;\n"
    )


def applications_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME", "").strip() or str(Path.home() / ".local" / "share")
    return Path(data) / "applications"


def make_linux(folder: Path | None = None) -> list[str]:
    folder = folder or applications_dir()
    folder.mkdir(parents=True, exist_ok=True)
    entry = folder / "chembook3d.desktop"
    text = desktop_entry(
        str(base_python()),
        str(Path(launcher.__file__)),
        str(launcher.REPO),
        str(ASSETS / "chembook3d.png"),
    )
    entry.write_text(text, encoding="utf-8")
    entry.chmod(0o755)
    update = shutil.which("update-desktop-database")
    if update:  # the menu picks it up sooner; not needed
        subprocess.run([update, str(folder)], capture_output=True)
    return [str(entry)]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="chembook3d shortcut",
        description="Create (or repair) the Chembook3D shortcut that starts the app without a "
        "terminal: it updates this checkout, rebuilds the interface when needed and opens the "
        "app in the browser; closing the browser stops it (D107).",
    )
    parser.parse_args(argv)
    if not (launcher.REPO / "pyproject.toml").is_file():
        sys.exit("The shortcut works from a git checkout of Chembook3D (see README.md).")
    uv = find_uv()
    if uv is None:
        sys.exit("uv was not found. Run this as `uv run chembook3d shortcut`.")
    browser = None
    try:
        if running_in_wsl():
            made, browser = make_wsl()
            where = "on the Windows desktop and in the Start menu (it starts it inside WSL)"
        elif sys.platform == "win32":
            made = make_windows()
            where = "on the desktop and in the Start menu"
        elif sys.platform.startswith("linux"):
            made = make_linux()
            where = "in the app menu"
        else:
            sys.exit(
                "Shortcuts are made on Windows, Linux and WSL only. On this system the launcher "
                f"can be run as: python3 -I {Path(launcher.__file__)}"
            )
    except (ShortcutError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"The shortcut could not be made: {exc}")
    settings = save_settings(launcher.config_dir(), uv, browser)
    for path in made:
        print(f"Created {path}")
    print(f"Saved the tools' locations in {settings}")
    print(
        f"Start Chembook3D with the {NAME} shortcut {where}. It updates this checkout, rebuilds "
        "the interface when needed and opens the app in your browser; closing the browser tab "
        "stops it. Run this command again if the shortcut stops working."
    )
