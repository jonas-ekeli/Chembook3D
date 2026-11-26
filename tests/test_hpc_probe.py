"""Exercise the fixed-executable alternative to a registered SSH subsystem (Q101)."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import asyncssh

from tests.fake_ssh_server import CODE, PASSWORD, USER, FakeSaga

PROBE = Path(__file__).resolve().parents[1] / "scripts" / "hpc_capability_probe.py"
LAUNCH = 'python3 -I "$HOME/.local/lib/chembook3d/hpc_capability_probe.py"'


class ProbeServer(FakeSaga):
    async def _session(self, process):
        # Map the fixed POSIX launcher to the local interpreter on either CI platform.
        if process.command != LAUNCH:
            return await super()._session(process)
        self.record.commands.append(process.command)
        data = await process.stdin.read(257)
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", str(PROBE)],
            input=data,
            capture_output=True,
            timeout=10,
            check=False,
        )
        process.stdout.write(result.stdout)
        process.exit(result.returncode)


class FixtureLogin(asyncssh.SSHClient):
    def kbdint_auth_requested(self):
        return ""

    def kbdint_challenge_received(self, name, instructions, lang, prompts):
        return [PASSWORD if prompt == "Password: " else CODE for prompt, _ in prompts]


def test_fixed_probe_on_one_authenticated_transport(tmp_path):
    server = ProbeServer(tmp_path).start()

    async def exercise():
        hosts = asyncssh.import_known_hosts(
            f"[127.0.0.1]:{server.port} {server.key.export_public_key().decode()}"
        )
        async with asyncssh.connect(
            "127.0.0.1",
            server.port,
            username=USER,
            known_hosts=hosts,
            client_factory=FixtureLogin,
            client_keys=[],
            agent_path=None,
            config=None,
        ) as connection:
            async with connection.start_sftp_client() as files:
                await files.stat("/")
                for request, allowed in [
                    ('{"operation":"capabilities"}', True),
                    ('{"operation":"submit"}', False),
                    ('{"operation":"capabilities","path":"sample"}', False),
                    ("x" * 257, False),
                ]:
                    reply = await connection.run(LAUNCH, input=request, timeout=10)
                    assert reply.exit_status == (0 if allowed else 1)
                    answer = json.loads(reply.stdout)
                    assert answer["ok"] is allowed
                    if allowed:
                        assert answer["protocol"] == "chembook3d-probe-v1"
                        assert answer["operations"] == ["capabilities"]

    try:
        asyncio.run(exercise())
        assert server.record.logins == 1
        assert server.record.commands == [LAUNCH] * 4
    finally:
        server.stop()
