"""`chembook3d shortcut` (D107, FR-RUN-01)."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from chembook3d import launcher, shortcut


def test_the_launcher_runs_with_a_python_that_uv_sync_leaves_alone():
    python = shortcut.base_python(windowless=True)
    assert python.is_file()
    if sys.platform == "win32":
        assert python.name.lower() == "pythonw.exe"  # no console window
    assert ".venv" not in python.parts


def test_settings_name_uv_and_the_tools_folders(tmp_path):
    path = shortcut.save_settings(tmp_path, "/opt/uv/uv", "/mnt/c/Windows/explorer.exe")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["uv"] == "/opt/uv/uv" and saved["browser"] == "/mnt/c/Windows/explorer.exe"
    git = shutil.which("git")
    assert git is not None and str(Path(git).parent) in saved["path"]
    assert launcher.load_settings(tmp_path) == saved  # what the launcher reads


def test_desktop_entry_arguments_are_quoted_by_the_specification():
    assert shortcut.desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
    assert shortcut.desktop_quote("/home/me/My Chembook3D") == '"/home/me/My Chembook3D"'
    assert shortcut.desktop_quote('/a "b"') == '"/a \\\\"b\\\\""'
    assert shortcut.desktop_quote("/a$b") == '"/a\\\\$b"'
    assert shortcut.desktop_quote("/100%") == "/100%%"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a Linux app menu entry")
def test_a_linux_menu_entry_runs_the_launcher_without_a_terminal(tmp_path):
    [made] = shortcut.make_linux(tmp_path / "applications")
    entry = Path(made)
    assert entry.name == "chembook3d.desktop" and os.access(entry, os.X_OK)
    lines = dict(line.split("=", 1) for line in entry.read_text(encoding="utf-8").splitlines()[1:])
    assert lines["Name"] == "Chembook3D" and lines["Terminal"] == "false"
    command = shlex.split(lines["Exec"])
    assert command[1:] == ["-I", str(Path(launcher.__file__))]
    assert Path(command[0]).is_file()
    assert Path(lines["Icon"]).is_file() and lines["Icon"].endswith(".png")


def test_the_wsl_script_hides_the_console_and_quotes_paths():
    text = shortcut.wsl_script("Ubuntu 24.04", "/usr/bin/python3", "/home/me/a b/launcher.py")
    assert text.endswith(
        'shell.Run "wsl.exe -d ""Ubuntu 24.04"" -e ""/usr/bin/python3"" -I '
        '""/home/me/a b/launcher.py""", 0, False\r\n'
    )


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows .lnk file")
def test_a_windows_shortcut_runs_the_launcher_with_its_icon(tmp_path):
    exe = shutil.which("powershell")
    assert exe is not None
    link = tmp_path / "Chembook3D.lnk"
    target = str(shortcut.base_python(windowless=True))
    arguments = f'-I "{Path(launcher.__file__)}"'
    icon = str(shortcut.ASSETS / "chembook3d.ico")
    shortcut.write_lnk(exe, str(link), target, arguments, str(launcher.REPO), icon)
    quoted = shortcut.ps_quote(str(link))
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({quoted});"
        " $s.TargetPath; $s.Arguments; $s.IconLocation"
    )
    lines = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert lines[0].lower() == target.lower()
    assert lines[1] == arguments
    assert lines[2].lower() == f"{icon},0".lower()
    folders = shortcut.windows_folders(exe)
    assert Path(folders["Desktop"]).is_dir() and Path(folders["Programs"]).is_dir()
