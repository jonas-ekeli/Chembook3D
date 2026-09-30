import pytest

from scripts import build_frontend


@pytest.mark.parametrize(
    ("version", "ok"),
    [
        ("v22.22.2", True),
        ("v24.1.0", True),
        ("v20.19.0", True),
        ("v20.18.1", False),
        ("v22.11.0", False),
        ("v21.7.3", False),
        ("v18.20.4", False),
    ],
)
def test_node_version_check(version, ok):
    assert build_frontend.node_supported(version) is ok


def test_windows_node_under_wsl_is_refused(monkeypatch, capsys):
    # Under WSL the Windows npm runs scripts through CMD.EXE, which cannot use a WSL folder.
    monkeypatch.setattr(build_frontend, "running_in_wsl", lambda: True)
    monkeypatch.setattr(
        build_frontend.shutil, "which", lambda name: f"/mnt/c/Program Files/nodejs/{name}"
    )
    with pytest.raises(SystemExit):
        build_frontend.check_tools()
    assert "Windows Node.js" in capsys.readouterr().err
