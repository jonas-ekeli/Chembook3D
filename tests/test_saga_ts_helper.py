"""Test the archived TS replay contract and its Linux-only submission boundary."""

import gzip
import hmac
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from chembook3d.remote import saga_prepare as helper

CHILD = (
    b"%chk=child\n# Synthetic TS\n\nreview\n\n0 1\n\n--Link1--\n"
    b"%chk=child\n# Synthetic Freq\n\nreview\n\n0 1\n\n"
)
CHECKPOINT = gzip.compress(b"synthetic checkpoint", mtime=0)
REVIEW = {
    "format": "chembook3d-saga-ts-v1",
    "parent_job_id": "834",
    "parent_stem": "parent",
    "input_name": "child.inp",
    "checkpoint_name": "child.fchk.gz",
    "input_sha256": helper.digest(CHILD),
    "checkpoint_sha256": helper.digest(CHECKPOINT),
    "output_sha256": helper.digest(b"synthetic frequency output"),
    "freeze_distance": [2, 4],
    "mode_index": 3,
    "frequency_cm1": -92.0,
    "bond_projection": -0.21,
    "alignment": 0.37,
}


class SagaReviewTest(unittest.TestCase):
    def test_review_shape(self):
        helper.validate_ts_review(REVIEW)
        for changes in (
            {"format": "other"},
            {"input_name": "../child.inp"},
            {"parent_stem": "x.inp"},
            {"parent_job_id": "0"},
            {"input_sha256": "0"},
            {"checkpoint_name": "parent.fchk.gz"},
            {"freeze_distance": [1, 1]},
            {"freeze_distance": [True, 4]},
            {"mode_index": False},
            {"frequency_cm1": 0},
            {"bond_projection": float("inf")},
            {"alignment": float("nan")},
            {"alignment": 2},
            {"script": "extra"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                helper.validate_ts_review({**REVIEW, **changes})

    def test_each_link_uses_only_the_receiving_basename(self):
        helper.ts_checkpoint_directives(CHILD, "child.inp")
        for data in (
            CHILD.replace(b"chk=child", b"chk=parent", 1),
            CHILD.replace(b"chk=child", b"chk=../child"),
            CHILD.replace(b"%chk=child", b"%oldchk=other\n%chk=child", 1),
            CHILD.replace(b"%chk=child", b"%chk=child\n%chk=child", 1),
            CHILD.split(b"--Link1--")[0],
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                helper.ts_checkpoint_directives(data, "child.inp")


@unittest.skipUnless(sys.platform.startswith("linux"), "Saga uses Linux descriptor semantics")
class SagaTSHelperTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.home = base / "home"
        projects = base / "projects"
        self.root = projects / "project/operator/chembook3d-pilot"
        self.root.mkdir(parents=True)
        self.home.mkdir(mode=0o700)
        for p in (
            patch.dict(os.environ, HOME=str(self.home)),
            patch.object(helper, "PROJECTS", projects),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.control = helper.control_dir()
        self.control.mkdir(parents=True, mode=0o700)
        self.policy = {
            "work_root": str(self.root),
            "stem": "sample",
            "attempt": "1" * 32,
            "reference_sha256": "a" * 64,
        }
        self.minimum = self.control / helper.MARKER
        helper.atomic_write(self.minimum, json.dumps(self.policy).encode())
        self.wrapper = self.home / helper.WRAPPER
        self.wrapper.parent.mkdir(parents=True)
        self.wrapper.write_bytes(
            b"#!/bin/sh\n"
            b'echo call >> "$HOME/calls"\n'
            b'[ "$*" = "child.inp -n 1 -c 40 -t 24" ] || exit 1\n'
            b'set -- ./*; [ "$#" = 2 ] && [ -f ./child.fchk.gz ] && [ -f ./child.inp ] || exit 1\n'
            b'echo "Submitted batch job 1234"\n'
        )
        self.wrapper.chmod(0o700)
        self.bundle = self.home / "bundle"
        self.bundle.mkdir()
        (self.bundle / "child.inp").write_bytes(CHILD)
        (self.bundle / "parent.fchk.gz").write_bytes(CHECKPOINT)
        (self.bundle / "review.json").write_bytes(helper.review_bytes(REVIEW))
        self.marker = self.control / helper.TS_MARKER

    def enroll(self):
        self.attempt = helper.enroll_ts(self.bundle)
        self.enabled = json.loads(self.marker.read_bytes())
        self.folder = self.root / ("ts-" + self.attempt)

    def request(self, operation, *, key=None, **fields):
        request = {"operation": operation, **{k: self.enabled[k] for k in helper.TS_IDENTITY}}
        if operation == "submit_ts":
            request.update(helper.RESOURCES)
        request.update(fields)
        if operation == "submit_ts":
            signed = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
            request["signature"] = hmac.digest(
                bytes.fromhex(key or self.enabled["key"]), signed, "sha256"
            ).hex()
        return request

    def invoke(self, operation, **fields):
        return helper.ts_operation(self.request(operation, **fields), self.policy)

    def test_review_replay_stages_only_approved_bytes_once(self):
        helper.enable_submit()
        minimum_key = json.loads((self.control / helper.SUBMIT_MARKER).read_bytes())["key"]
        original_minimum = self.minimum.read_bytes()
        self.enroll()
        enabled = self.marker.read_bytes()
        self.assertNotEqual(self.enabled["key"], minimum_key)
        self.assertEqual(self.marker.stat().st_mode & 0o777, 0o600)
        self.assertEqual(helper.enroll_ts(self.bundle), self.attempt)
        self.assertEqual(self.marker.read_bytes(), enabled)
        self.assertEqual(self.minimum.read_bytes(), original_minimum)
        self.assertEqual(
            json.loads((self.control / helper.SUBMIT_MARKER).read_bytes())["key"], minimum_key
        )
        with self.assertRaises((OSError, ValueError)):
            self.invoke("submit_ts")
        (self.bundle / "parent.fchk.gz").unlink()
        preview = self.invoke("prepare_ts")
        self.assertEqual(preview, self.invoke("prepare_ts"))
        self.assertEqual(preview["review"], REVIEW)
        self.assertEqual(preview["input"].encode(), CHILD)
        self.assertEqual(preview["directory"], str(self.folder))
        self.assertEqual(list(self.folder.iterdir()), [self.folder / "child.inp"])
        self.assertEqual(self.invoke("ts_status")["state"], "prepared")
        with self.assertRaisesRegex(ValueError, "authorization"):
            self.invoke("submit_ts", key=minimum_key)
        (self.folder / "child.fchk.gz").write_bytes(b"stale public checkpoint")
        with ThreadPoolExecutor(max_workers=6) as threads:
            results = list(threads.map(lambda _: self.invoke("submit_ts"), range(6)))
        self.assertTrue(any(answer["state"] == "submitted" for answer in results))
        receipt = self.invoke("ts_status")
        self.assertEqual(receipt["state"], "submitted")
        self.assertEqual(receipt["job_id"], "1234")
        stage = self.folder / "submission"
        self.assertEqual(receipt["directory"], str(stage))
        self.assertEqual((stage / "child.fchk.gz").read_bytes(), CHECKPOINT)
        self.assertEqual((stage / "child.inp").read_bytes(), CHILD)
        self.wrapper.unlink()
        (self.folder / "child.inp").unlink()
        self.assertEqual(self.invoke("submit_ts"), receipt)
        self.assertEqual(self.invoke("ts_status"), receipt)
        with self.assertRaisesRegex(ValueError, "get_prepared_job"):
            self.invoke("prepare_ts")
        (self.control / ("ts-" + self.attempt + ".submit.done")).unlink()
        self.assertEqual(self.invoke("submit_ts")["state"], "uncertain")
        self.assertEqual((self.home / "calls").read_bytes(), b"call\n")

    def test_modified_sources_and_changed_wrapper_refused_before_claim(self):
        self.enroll()
        self.invoke("prepare_ts")
        checkpoint = self.control / ("ts-" + self.attempt) / "parent.fchk.gz"
        checkpoint.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.invoke("submit_ts")
        checkpoint.write_bytes(CHECKPOINT)
        preview = self.folder / "child.inp"
        preview.write_bytes(CHILD + b"\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.invoke("submit_ts")
        preview.write_bytes(CHILD)
        self.wrapper.write_bytes(b"#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(ValueError, "wrapper has changed"):
            self.invoke("submit_ts")
        self.assertFalse((self.folder / "submission").exists())
        self.assertFalse((self.home / "calls").exists())

    def test_operator_cli_enrollment_and_request_dispatch(self):
        script = self.control / "saga_prepare.py"
        script.write_text(
            Path(helper.__file__)
            .read_text()
            .replace(
                'PROJECTS = Path("/cluster/projects")', f"PROJECTS = Path({str(helper.PROJECTS)!r})"
            )
        )
        command = [sys.executable, str(script), "enroll-ts", str(self.bundle)]
        with ThreadPoolExecutor(max_workers=2) as threads:
            replies = list(threads.map(lambda _: subprocess.check_output(command), range(2)))
        self.assertEqual(replies[0], replies[1])
        self.enabled = json.loads(self.marker.read_bytes())
        self.assertIn(self.enabled["attempt"].encode(), replies[0])
        self.assertNotIn(self.enabled["key"].encode(), replies[0])
        for operation, state in [
            ("prepare_ts", "prepared"),
            ("submit_ts", "submitted"),
            ("ts_status", "submitted"),
        ]:
            reply = subprocess.check_output(
                [sys.executable, str(script)], input=json.dumps(self.request(operation)).encode()
            )
            self.assertEqual(len(reply.splitlines()), 1)
            message = json.loads(reply)
            self.assertIs(message["ok"], True)
            self.assertEqual(message["result"]["state"], state)

    def test_different_pair_or_inexact_request_rejected(self):
        self.enroll()
        (self.bundle / "review.json").write_bytes(helper.review_bytes({**REVIEW, "mode_index": 2}))
        with self.assertRaisesRegex(ValueError, "different TS"):
            helper.enroll_ts(self.bundle)
        for fields in (
            {"review_sha256": "d" * 64},
            {"checkpoint_sha256": "d" * 64},
            {"revision": "c" * 64},
            {"nodes": True},
            {"hours": 25},
            {"attempt": "c" * 32},
            {"input_name": REVIEW["input_name"]},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.invoke("submit_ts", **fields)
        for operation in ("prepare_ts", "ts_status"):
            with self.assertRaises(ValueError):
                self.invoke(operation, path="/tmp")
        self.minimum.write_bytes(json.dumps({**self.policy, "attempt": "e" * 32}).encode())
        with self.assertRaisesRegex(ValueError, "different minimum"):
            self.invoke("prepare_ts")
        self.assertFalse(self.folder.exists())

    def test_bad_bundle_and_directory_links_rejected(self):
        checkpoint = self.bundle / "parent.fchk.gz"
        checkpoint.write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "differs"):
            self.enroll()
        checkpoint.rename(self.home / "target")
        checkpoint.symlink_to(self.home / "target")
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.enroll()
        self.assertFalse(self.marker.exists())
