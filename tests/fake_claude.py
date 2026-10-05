"""A stand-in for the `claude` CLI in the Claude panel tests (D92). It writes how it was
started to `fake-claude.json` in its working folder (a terminal may re-wrap long printed
lines), then answers each line typed until it reads "exit"."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
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
