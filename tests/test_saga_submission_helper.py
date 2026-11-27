"""Exercise the standalone helper and a synthetic scheduler on a local Linux filesystem."""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from chembook3d.remote import saga_prepare as helper


@unittest.skipUnless(sys.platform.startswith("linux"), "Saga uses Linux descriptor semantics")
class SagaSubmissionTest(unittest.TestCase):
    def setUp(self):
        local = tempfile.TemporaryDirectory()
        self.addCleanup(local.cleanup)
        base = Path(local.name)
        self.home = base / "home"
        self.projects = base / "projects"
        self.root = self.projects / "project/operator/chembook3d-pilot"
        env = patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        projects = patch.object(helper, "PROJECTS", self.projects)
        projects.start()
        self.addCleanup(projects.stop)
        baseline = self.root / "min-test-local"
        baseline.mkdir(parents=True)
        inp = baseline / "sample.inp"
        inp.write_bytes(b"# HF/STO-3G Opt\n\nSynthetic H2\n\n0 1\nH 0 0 0\nH 0 0 0.74\n\n")
        (baseline / "sample.xyz").write_bytes(b"2\nSynthetic H2\nH 0 0 0\nH 0 0 0.74\n")
        job = self.root.parent / "jobdescriptions" / helper.JOB
        job.parent.mkdir()
        job.write_bytes(b"Synthetic local optimization\n")
        generator = self.home / "tools/scripts/submit_all_DFT.sh"
        generator.parent.mkdir(parents=True)
        generator.write_bytes(
            b"#!/bin/sh\n[ \"$3\" = nosub ] || exit 1\ncat > sample.inp <<'INPUT'\n"
            + inp.read_bytes()
            + b"INPUT\n"
        )
        generator.chmod(0o700)
        self.wrapper = self.home / helper.WRAPPER
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_bytes(
            b"#!/bin/sh\n"
            b'echo call >> "$HOME/calls"\n'
            b'[ "$#" = 7 ] && [ "$*" = "sample.inp -n 1 -c 40 -t 24" ] || exit 10\n'
            b'set -- ./*; [ "$#" = 1 ] && [ "$1" = ./sample.inp ] || exit 11\n'
            b'pwd > "$HOME/directory"\n'
            b'while [ -f "$HOME/pause" ]; do sleep 0.02; done\n'
            b'cat "$HOME/reply"\n'
        )
        self.wrapper.chmod(0o700)
        self.reply = self.home / "reply"
        self.reply.write_text("wrapper notice\nSubmitted batch job 61024\n")
        self.revision = helper.install(inp)
        self.control = helper.control_dir()
        self.policy = json.loads((self.control / helper.MARKER).read_bytes())
        self.attempt = self.policy["attempt"]
        self.folder = self.root / ("prepare-" + self.attempt)
        self.stage = self.folder / "submission"
        self.marker = self.control / helper.SUBMIT_MARKER
        self.script = self.control / "saga_prepare.py"
        self.script.write_text(
            self.script.read_text().replace(
                'PROJECTS = Path("/cluster/projects")', f"PROJECTS = Path({str(self.projects)!r})"
            )
        )

    def cli(self, *args, request=None, success=True):
        result = subprocess.run(
            [sys.executable, str(self.script), *args],
            input=b"" if request is None else json.dumps(request).encode(),
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        if args and success:
            return result.stdout.decode()
        result = json.loads(result.stdout)
        self.assertIs(result["ok"], success)
        return result["result" if success else "error"]

    def enable(self):
        response = self.cli("enable-submit")
        self.assertIn(self.attempt, response)
        key = json.loads(self.marker.read_bytes())["key"]
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertNotIn(key, response)
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        return key

    def prepare(self):
        self.cli(request={"operation": "prepare_minimum"})

    def request(self, submit=False, **overrides):
        fields = {
            "attempt": self.attempt,
            "revision": self.revision,
            "operation": "submit_minimum" if submit else "minimum_status",
        }
        if not submit:
            return {**fields, **overrides}
        data = {**fields, "nodes": 1, "cores": 40, "hours": 24, **overrides}
        key = bytes.fromhex(json.loads(self.marker.read_bytes())["key"])
        signed = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        return {**data, "signature": hmac.digest(key, signed, "sha256").hex()}

    def test_success_and_recovery_after_preparation_and_wrapper_change(self):
        key = self.enable()
        self.assertEqual(self.enable(), key)
        self.prepare()
        (self.folder / "sample.chk").write_bytes(b"unapproved matching checkpoint")
        status = self.request()
        submit = self.request(submit=True)
        expected = {
            "attempt": self.attempt,
            "revision": self.revision,
            "state": "prepared",
            "directory": str(self.stage),
            "nodes": 1,
            "cores": 40,
            "hours": 24,
            "job_id": None,
            "detail": None,
        }
        self.assertEqual(self.cli(request=status), expected)
        self.assertFalse(self.stage.exists())
        receipt = {**expected, "state": "submitted", "job_id": "61024"}
        self.assertEqual(self.cli(request=submit), receipt)
        self.assertEqual((self.home / "directory").read_text().strip(), str(self.stage))
        self.assertEqual(list(self.stage.iterdir()), [self.stage / "sample.inp"])
        self.assertEqual(
            hashlib.sha256((self.stage / "sample.inp").read_bytes()).hexdigest(), self.revision
        )
        (self.folder / "sample.inp").unlink()
        self.wrapper.unlink()
        self.assertEqual(self.cli(request=status), receipt)
        self.assertEqual(self.cli(request=submit), receipt)
        result_file = self.control / (self.attempt + ".submit.done")
        result_file.write_bytes(b'{"state":"submitted","job_id":null,"detail":null}')
        self.assertEqual(self.cli(request=submit)["state"], "uncertain")
        result_file.unlink()
        self.assertEqual(self.cli(request=submit)["state"], "uncertain")
        self.assertEqual(self.cli(request=status)["state"], "uncertain")
        self.assertEqual((self.home / "calls").read_text(), "call\n")

    def test_disabled_unprepared_unsigned_or_mismatched_requests(self):
        status = self.request()
        self.assertIn("not enabled", self.cli(request=status, success=False))
        self.enable()
        submit = self.request(submit=True)
        self.cli(request=submit, success=False)
        self.prepare()
        bad = [
            {k: v for k, v in submit.items() if k != "signature"},
            {**submit, "signature": "0" * 64},
            {**status, "signature": submit["signature"]},
        ]
        for values in (
            {"attempt": "0" * 32},
            {"revision": "0" * 64},
            {"nodes": True},
            {"cores": 39},
            {"hours": 25},
            {"hours": 24.0},
            {"path": str(self.stage)},
        ):
            bad.append(self.request(submit=True, **values))
        for fields in bad:
            with self.subTest(fields=list(fields)):
                self.cli(request=fields, success=False)
        done = self.control / (self.attempt + ".done")
        done.unlink()
        self.cli(request=submit, success=False)
        done.write_bytes(self.revision.encode())
        done.chmod(0o600)
        inp = self.folder / "sample.inp"
        inp.write_bytes(b"changed input")
        self.cli(request=submit, success=False)
        inp.unlink()
        inp.symlink_to(self.root / "min-test-local/sample.inp")
        self.cli(request=submit, success=False)
        self.assertFalse((self.home / "calls").exists())
        self.assertFalse(self.stage.exists())

    def test_wrapper_pin_symlinks_and_private_permissions(self):
        installed = self.home / "installed-wrapper"
        self.wrapper.rename(installed)
        self.wrapper.symlink_to(installed)
        self.control.chmod(0o2755)
        self.enable()
        self.prepare()
        submit = self.request(submit=True)
        self.marker.chmod(0o644)
        self.cli(request=submit, success=False)
        self.marker.chmod(0o600)
        self.control.chmod(0o770)
        self.cli(request=submit, success=False)
        self.control.chmod(0o700)
        installed.write_bytes(installed.read_bytes() + b"# wrapper changed\n")
        self.cli("enable-submit", success=False)
        self.cli(request=submit, success=False)
        self.wrapper.unlink()
        outside = self.root / "unsafe"
        installed.rename(outside)
        self.wrapper.symlink_to(outside)
        self.cli("enable-submit", success=False)
        self.cli(request=submit, success=False)
        self.assertFalse((self.home / "calls").exists())

    def test_saga_group_writable_installation_below_private_home(self):
        target = self.wrapper.parent.parent / "source/job_submission/src/g16-C.01.sh"
        target.parent.mkdir(parents=True)
        self.wrapper.rename(target)
        self.wrapper.symlink_to("../source/job_submission/src/g16-C.01.sh")
        self.home.chmod(0o2700)
        for parent in (self.wrapper.parent, *target.parents):
            if parent == self.home:
                break
            parent.chmod(0o2775)
        target.chmod(0o775)
        before = set(self.control.iterdir())
        self.assertEqual(
            json.loads(self.cli("check-wrapper")),
            {
                "ok": True,
                "wrapper": str(target),
                "wrapper_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            },
        )
        self.assertEqual(set(self.control.iterdir()), before)
        self.assertFalse(self.folder.exists())
        self.cli(request={"operation": "check-wrapper"}, success=False)
        self.enable()
        self.prepare()
        submit = self.request(submit=True)
        self.home.chmod(0o2710)
        self.cli("check-wrapper", success=False)
        self.cli(request=submit, success=False)
        self.home.chmod(0o2700)
        self.assertFalse((self.home / "calls").exists())
        self.assertEqual(self.cli(request=submit)["state"], "submitted")
        self.assertEqual((self.home / "calls").read_text(), "call\n")

    def test_private_home_exception_stops_at_its_boundary(self):
        self.home.chmod(0o2700)
        self.wrapper.chmod(0o777)
        self.cli("check-wrapper", success=False)
        self.wrapper.chmod(0o775)
        alias = self.home.parent / "also-accessible-outside-home"
        os.link(self.wrapper, alias)
        self.cli("check-wrapper", success=False)
        alias.unlink()
        self.wrapper.parent.chmod(0o777)
        self.cli("check-wrapper", success=False)
        self.wrapper.parent.chmod(0o775)
        self.home.parent.chmod(0o770)
        self.cli("check-wrapper", success=False)
        self.home.parent.chmod(0o700)
        self.wrapper.rename(alias)
        self.wrapper.symlink_to(alias)
        self.cli("check-wrapper", success=False)
        self.assertFalse(self.marker.exists())

    def test_preexisting_staging_link_is_consumed_without_launch(self):
        self.enable()
        self.prepare()
        self.stage.symlink_to(self.home, target_is_directory=True)
        submit = self.request(submit=True)
        refused = self.cli(request=submit)
        self.assertEqual(refused["state"], "rejected")
        self.stage.unlink()
        self.assertEqual(self.cli(request=submit), refused)
        self.assertFalse((self.home / "calls").exists())
        self.assertFalse((self.home / "sample.inp").exists())

    def test_concurrent_enable_and_submit_share_one_key_and_one_scheduler_call(self):
        self.prepare()
        with ThreadPoolExecutor(max_workers=3) as pool:
            keys = list(pool.map(lambda _: self.enable(), range(3)))
            self.assertEqual(len(set(keys)), 1)
            submit = self.request(submit=True)
            gate = self.home / "pause"
            gate.touch()
            first = pool.submit(self.cli, request=submit)
            try:
                deadline = time.monotonic() + 5
                while not (self.home / "calls").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((self.home / "calls").exists())
                self.assertEqual(self.cli(request=submit)["state"], "uncertain")
                self.assertEqual(self.cli(request=self.request())["state"], "uncertain")
            finally:
                gate.unlink()
            self.assertEqual(first.result()["job_id"], "61024")
        self.assertEqual((self.home / "calls").read_text(), "call\n")

    def test_oversized_reply_with_receipt_stays_uncertain(self):
        self.enable()
        self.prepare()
        self.reply.write_bytes(self.reply.read_bytes() + b"!" * helper.OUTPUT_LIMIT)
        submit = self.request(submit=True)
        outcome = self.cli(request=submit)
        self.assertEqual(outcome["state"], "uncertain")
        self.assertIsNone(outcome["job_id"])
        self.assertEqual(self.cli(request=submit), outcome)
        self.assertEqual((self.home / "calls").read_text(), "call\n")

    def test_scheduler_rejection_is_terminal_even_when_wrapper_exits_zero(self):
        self.enable()
        self.prepare()
        self.reply.write_bytes(b"sbatch: error: Batch job submission failed: Invalid account\n")
        submit = self.request(submit=True)
        outcome = self.cli(request=submit)
        self.assertEqual(outcome["state"], "rejected")
        self.assertIsNone(outcome["job_id"])
        self.reply.write_bytes(b"Submitted batch job 234\n")
        self.assertEqual(self.cli(request=submit), outcome)
        self.assertEqual(self.cli(request=self.request()), outcome)
        self.assertEqual((self.home / "calls").read_text(), "call\n")


class SchedulerReceiptTest(unittest.TestCase):
    def test_receipt_requires_one_unambiguous_stdout_line(self):
        accepted = b"Submitted batch job 1234\n"
        prefix = b"sbatch: error: Batch job submission failed: "
        reject = prefix + b"Invalid account or account/partition combination specified\n"
        for stdout, stderr, expected in (
            (accepted, b"", "submitted"),
            (b"", reject, "rejected"),
            (accepted, reject, "uncertain"),
            (accepted * 2, b"", "uncertain"),
            (b"Submitted batch job 1234?\n", b"", "uncertain"),
            (b"", accepted, "uncertain"),
            (b"", accepted + reject, "uncertain"),
            (b"", prefix + b"Socket timed out on send/recv operation\n", "uncertain"),
            (b"", b"", "uncertain"),
        ):
            with self.subTest(stdout=stdout, stderr=stderr):
                state, job_id, _ = helper.scheduler_outcome(stdout, stderr)
                self.assertEqual(state, expected)
                self.assertEqual(job_id, "1234" if state == "submitted" else None)


if __name__ == "__main__":
    unittest.main()
