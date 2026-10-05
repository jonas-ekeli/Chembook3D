"""A stand-in for `claude --cloud "<task>"` in the cloud job tests (D93). It appends how it
was started to the file named by FAKE_CLAUDE_LOG and prints what the real CLI prints after
creating a session, or, with FAKE_CLAUDE_CLOUD=fail, an error."""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    log = Path(os.environ["FAKE_CLAUDE_LOG"])
    with log.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
    if os.environ.get("FAKE_CLAUDE_CLOUD") == "fail":
        print("\x1b[31mError: Unable to get organization UUID\x1b[0m", flush=True)
        return 1
    print("Creating remote session…", flush=True)
    print("Created cloud session: Run the Chembook3D calculation job", flush=True)
    print("Session ID: session_01FakeCloudJob42", flush=True)
    print("View: https://claude.ai/code/session_01FakeCloudJob42?from=cli&m=0", flush=True)
    print("Resume with: claude --teleport session_01FakeCloudJob42", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
