from chembook3d import cli


def test_server_binds_to_loopback_only():
    # NFR-SEC-01: the backend must never listen on external interfaces.
    assert cli.HOST == "127.0.0.1"


def test_wsl_opens_the_windows_browser(monkeypatch):
    # Under WSL xdg-open has no browser and prints a gio error; explorer.exe opens the Windows one.
    calls = []
    monkeypatch.setattr(cli, "running_in_wsl", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/mnt/c/Windows/{name}")
    monkeypatch.setattr(cli.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: calls.append(["webbrowser", url]))
    cli.open_browser("http://127.0.0.1:8765/")
    assert calls == [["/mnt/c/Windows/explorer.exe", "http://127.0.0.1:8765/"]]
