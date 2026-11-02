"""Remote POSIX names must also be safe on Windows, where the keeper may run."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from chembook3d.remote import keeper


@pytest.mark.parametrize(
    "name",
    [
        r"..\escaped.log",
        r"C:\escaped.log",
        "C:escaped.log",
        r"\\host\share\x.log",
        r"\rooted.log",
        "output.log:stream",
        "CON",
        "nul.log",
        "COM1.out",
        "LPT9.xyz",
        "con .log",
        "COM¹.log",
        "AUX.log",
        "trailing.",
        "trailing ",
        "x\x00.log",
    ],
)
def test_remote_download_refuses_windows_path_and_device_names(tmp_path, name):
    with pytest.raises(OSError, match="safely"):
        keeper._download_destination(tmp_path, name)
    assert not list(tmp_path.iterdir())


def completed(coroutine):
    """The in-memory SFTP fake completes immediately, without a network event loop."""
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    pytest.fail("unexpected asynchronous wait in in-memory SFTP fake")


def test_actual_download_keeps_original_paths_and_companion_names(tmp_path):
    downloaded = []

    async def stat(path):
        return SimpleNamespace(size=3, mtime=123)

    async def get(remote, local):
        target = Path(local)
        assert target.resolve().parent == tmp_path.resolve()
        target.write_bytes(b"abc")
        downloaded.append((remote, target.name))

    async def files():
        return SimpleNamespace(stat=stat, get=get)

    session = SimpleNamespace(files=files, used=0)
    ordinary = ["water.log", "job.out", "job_IRC_F_trj.xyz", "job_IRC_B_trj.xyz", "Ru CAAC ø.log"]
    unsafe = [r"..\escaped.log", r"C:\escaped.log", "file:stream", "nul.out"]
    paths = [f"/remote/{name}" for name in ordinary + unsafe]
    answer = completed(keeper.Keeper._download(None, session, paths, str(tmp_path)))
    assert answer["ok"]
    assert downloaded == [(f"/remote/{name}", name) for name in ordinary]
    assert [entry["path"] for entry in answer["files"]] == paths[: len(ordinary)]
    assert [entry["path"] for entry in answer["failed"]] == paths[len(ordinary) :]
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(ordinary)


def test_download_destination_refuses_existing_symlink_outside_folder(tmp_path):
    outside = tmp_path / "outside.log"
    outside.write_text("keep")
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    try:
        (downloads / "job.log").symlink_to(outside)
    except OSError:
        pytest.skip("creating symlinks requires Windows developer mode")
    with pytest.raises(OSError, match="outside"):
        keeper._download_destination(downloads, "job.log")
    assert outside.read_text() == "keep"
