"""A stand-in for the `claude` CLI in the Claude panel tests (D92). It writes how it was
started to `fake-claude.json` in its working folder (a terminal may re-wrap long printed
lines), then answers each line typed until it reads "exit". Started with --cloud, it is
`fake_claude_cloud.py`."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    if "--cloud" in sys.argv:  # a cloud job's launch (D93), in the UI tests
        sys.path.insert(0, str(Path(__file__).parent))
        from fake_claude_cloud import main as cloud

        return cloud()
    size = os.get_terminal_size(sys.stdout.fileno())
    started = {"args": sys.argv[1:], "cwd": os.getcwd(), "rows": size.lines, "cols": size.columns}
    Path("fake-claude.json").write_text(json.dumps(started), encoding="utf-8")
    print("Fake Claude ready", flush=True)
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "exit":
            print("Fake Claude bye", flush=True)
            return 3
        print(f"Fake Claude got: {line.strip()}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
