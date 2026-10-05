"""A stand-in for `claude --cloud "<task>"` in the cloud job tests (D93). It appends how it
was started to the file named by FAKE_CLAUDE_LOG and, like the real CLI, refuses to create a
session without a terminal. In one it prints what the real CLI prints after creating a session,
or as FAKE_CLAUDE_CLOUD says: "fail" prints an error, "attach" keeps following the session,
"ask" asks a question and waits for an answer."""

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    log = Path(os.environ["FAKE_CLAUDE_LOG"])
    with log.open("a", encoding="utf-8") as out:
        out.write(
            json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(), "tty": sys.stdout.isatty()})
            + "\n"
        )
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "Error: --cloud requires an interactive terminal. Non-interactive invocations "
            "(piped stdout, --init-only, --sdk-url) run locally and would silently ignore "
            "--cloud.",
            file=sys.stderr,
            flush=True,
        )
        return 1
    mode = os.environ.get("FAKE_CLAUDE_CLOUD")
    if mode == "fail":
        print("\x1b[31mError: Unable to get organization UUID\x1b[0m", flush=True)
        return 1
    if mode == "ask":
        print("\x1b[2GAccessing\x1b[12Gworkspace:\r\n\x1b[2GQuick safety check", flush=True)
        print(" > No, exit\r\n   Yes, I trust this folder", flush=True)
        sys.stdin.readline()
        time.sleep(60)
        return 0
    print("\x1b[?25l\x1b[2K\x1b[GCreating cloud session...", flush=True)
    print("Created cloud session: Run the Chembook3D calculation job", flush=True)
    print("Session ID: session_01FakeCloudJob42", flush=True)
    print("View: https://claude.ai/code/session_01FakeCloudJob42?from=cli&m=0", flush=True)
    if mode == "attach":
        time.sleep(60)
        return 0
    print("Resume with: claude --teleport session_01FakeCloudJob42", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
