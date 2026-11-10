"""The one rule for download file names built from the user's text."""

import json
from pathlib import Path

import pytest

from chembook3d.api.downloads import attachment, file_name


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("int2'", "int2'"),
        ("TS1-2", "TS1-2"),  # Jonas's primes keep these three apart
        ("TS1-2'", "TS1-2'"),
        ("TS1-2''", "TS1-2''"),
        ("TS1–2‡ β-H", "TS1–2‡ β-H"),
        ('<>:"/\\|?*', "_________"),
        ("a\tb\x7f", "a_b_"),
        ("  name. . ", "name"),
        ("...", "default"),
        ("", "default"),
    ],
)
def test_only_what_windows_refuses_is_replaced(text, name):
    assert file_name(text, "default") == name


# The examples the frontend's `fileName` (frontend/src/util.ts) is tested on as well
# (frontend/tests/util.test.mjs), so the two copies of the rule cannot drift apart.
SHARED = json.loads(
    (Path(__file__).parent / "fixtures" / "file-names.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda case: repr(case["text"][:40]))
def test_shared_file_name_examples(case):
    assert file_name(case["text"], SHARED["fallback"]) == case["name"]


def test_attachment_carries_the_real_name():
    assert attachment("int2' α.xyz") == (
        "attachment; filename=\"int2' _.xyz\"; filename*=UTF-8''int2%27%20%CE%B1.xyz"
    )
