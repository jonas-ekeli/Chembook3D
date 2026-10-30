"""The showcase demo's manifest and downloads (D170). Building the investigation needs the
ioChem-BD files, about 40 MB, so CI checks the manifest against the script's plan and the
download checks; `scripts/make_showcase_demo.py` builds it by hand."""

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "iochem"
KINDS = ("opt-freq", "sp-pbe-d3mbj", "sp-m06l-d3")


@pytest.fixture(scope="module")
def demo():
    spec = importlib.util.spec_from_file_location(
        "make_showcase_demo", ROOT / "scripts" / "make_showcase_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_manifest_pins_every_file_the_demo_imports(demo):
    files = demo.load_manifest()["files"]
    names = [entry["file"] for entry in files]
    assert len(names) == len(set(names))
    by_species = {}
    for entry in files:
        assert entry["url"].startswith("https://iochem-bd.bsc.es/browse/bitstream/100/")
        assert re.fullmatch(r"100/\d+", entry["handle"])
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        assert entry["file"] == f"{entry['species']}_{entry['kind']}.cml"
        by_species.setdefault(entry["species"], set()).add(entry["kind"])
    # Each structure the script places, and ethylene, with its optimization and both single
    # points, and nothing the script does not use.
    assert set(by_species) == {*demo.NODES, demo.ETHYLENE}
    assert all(kinds == set(KINDS) for kinds in by_species.values())
    for _, members in demo.GROUPS.values():
        assert set(members) <= set(demo.NODES)
    for source, target, _ in demo.EDGES:
        assert {source, target} <= {*demo.NODES, *demo.GROUPS}
    assert demo.CYCLE[0] == demo.CYCLE[-1]
    # Every card on the canvas has a place: the nodes outside groups, and the groups.
    grouped = {m for _, members in demo.GROUPS.values() for m in members}
    assert set(demo.PLACES) == (set(demo.NODES) - grouped) | set(demo.GROUPS)
    assert len(set(demo.PLACES.values())) == len(demo.PLACES)


def test_a_published_file_is_kept_once_it_matches(demo, tmp_path, monkeypatch):
    source = FIXTURES / "ethylene_opt-freq.cml"
    entry = {
        "file": "ethylene_opt-freq.cml",
        "url": "https://iochem-bd.bsc.es/browse/bitstream/100/200014/4/output.cml",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    fetched = []

    def download(url, target):
        fetched.append(url)
        target.write_bytes(source.read_bytes())

    monkeypatch.setattr(demo, "_download", download)
    assert demo.fetch(entry, tmp_path).read_bytes() == source.read_bytes()
    demo.fetch(entry, tmp_path)
    assert len(fetched) == 1  # the cached copy is checked, not downloaded again


def test_a_changed_file_is_refused(demo, tmp_path, monkeypatch):
    entry = {"file": "x.cml", "url": "https://iochem-bd.bsc.es/x", "sha256": "0" * 64}
    monkeypatch.setattr(demo, "_download", lambda url, target: target.write_bytes(b"other"))
    with pytest.raises(RuntimeError, match="has changed since the manifest was written"):
        demo.fetch(entry, tmp_path)
    assert not (tmp_path / "x.cml").exists()


def test_custom_basis_sets_and_dispersion_get_the_papers_names(demo):
    plan = {
        "names": [
            {"kind": "basis", "key": "a", "title": "CAAC1_CH2_Ar, Step4"},
            {"kind": "basis", "key": "b", "title": "CAAC1_CH2_Ar_SP, SP CHLOROFORM"},
            {"kind": "dispersion", "key": "c", "title": "CAAC1_CH2_Ar_SP, SP CHLOROFORM"},
        ]
    }
    assert demo._names(plan) == {
        "basis_names": {"a": "cc-pVDZ(-PP)", "b": "cc-pVQZ(-PP)"},
        "dispersion_names": {"c": "D3M(BJ)"},
    }


def test_the_steric_atoms_are_found_from_the_structure(demo):
    # RuCl2(L)(=CH2) with L cut down to the carbene carbon and its two nitrogens.
    xyz = """9
methylidene
Ru 0.00 0.00 0.00
Cl 2.30 0.00 0.00
Cl -2.30 0.00 0.00
C 0.00 0.00 -1.95
N 1.10 0.00 -2.70
N -1.10 0.00 -2.70
C 0.00 0.00 1.85
H 0.00 0.95 2.40
H 0.00 -0.95 2.40
"""
    assert demo.ligand_atoms(xyz) == {
        "centre": [1],
        "z_axis": [4],
        "xz_plane": [7],
        "excluded": [2, 3, 7, 8, 9],
    }
