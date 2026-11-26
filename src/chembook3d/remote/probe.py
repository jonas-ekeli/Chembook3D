"""User-invoked Q101 diagnostic. Only the keeper owns the authenticated transport."""

import json
import shlex

import asyncssh

REPLY = {"ok": True, "protocol": "chembook3d-probe-v1", "operations": ["capabilities"]}

# The command has no caller-controlled fields. Check and run the same bytes in an
# isolated interpreter, even if a file in the account changes during the probe.
PROBE_SOURCE = """\
import hashlib
from pathlib import Path
import sys
path = Path.home() / ".local/lib/chembook3d/hpc_capability_probe.py"
try:
    with path.open("rb") as stream:
        source = stream.read(4097).replace(b"\\r\\n", b"\\n")
except OSError:
    sys.exit(3)
expected = "920c212382a13d007d678c87b6291cd3622d3d34bcecb69087dfce1d638d6f04"
if hashlib.sha256(source).hexdigest() != expected:
    sys.exit(4)
exec(compile(source, str(path), "exec"), {"__name__": "__main__"})
"""
COMMAND = "python3 -I -S -c " + shlex.quote(PROBE_SOURCE)


async def check(connection) -> dict:
    try:
        result = await connection.run(
            COMMAND, input='{"operation":"capabilities"}\n', encoding="utf-8", timeout=12
        )
        if result.exit_status in (3, 4):
            return {
                "ok": False,
                "error": "Install the branch's probe at "
                "~/.local/lib/chembook3d/hpc_capability_probe.py.",
            }
        if result.exit_status != 0 or json.loads(result.stdout) != REPLY:
            return {"ok": False, "error": "The installed probe did not return its expected reply."}
    except (OSError, ValueError, TimeoutError, asyncssh.Error):
        return {"ok": False, "error": "The probe channel failed. Check the existing HPC login."}
    return {"ok": True, "probe": REPLY}
