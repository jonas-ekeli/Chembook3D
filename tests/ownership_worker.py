"""Real-process ownership test helper; file barriers do not require loopback sockets."""

import sys
import time
from pathlib import Path

from chembook3d import investigation as inv


def wait(path: Path) -> None:
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() > deadline:
            raise RuntimeError(f"timed out waiting for {path}")
        time.sleep(0.02)


def main() -> None:
    root, identity, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if mode == "race":
        migrate = inv._migrate

        def held_migrate(*args, **kwargs):
            (root / f"migrating-{identity}").touch()
            wait(root / "release")
            return migrate(*args, **kwargs)

        inv._migrate = held_migrate
        (root / f"ready-{identity}").touch()
        wait(root / "start")
    try:
        investigation = inv.open_investigation(root / "inv")
    except inv.InvestigationLocked:
        (root / f"result-{identity}").write_text("locked")
        return
    if mode == "hold":
        (root / f"ready-{identity}").touch()
        wait(root / "release")
    (root / f"result-{identity}").write_text("opened")
    investigation.close()


if __name__ == "__main__":
    main()
