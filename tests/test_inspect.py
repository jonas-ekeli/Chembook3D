"""The Inspect window (D123c, FR-3D-11, T-IMP-24): files read without importing them, cut-off
outputs of running jobs included, and xTB's optimization log with its convergence criteria."""

import shutil

import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.parsers import xtb
from chembook3d.services import inspection
from tests.test_orca_xtb_parsers import FIXTURES, read

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}
AZIRIDINIUM = FIXTURES / "gaussian" / "aziridinium-phos-full-c1.log"


@pytest.fixture
def page():
    """The app's own page: the Inspect window is not offered to anything else."""
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _cut(text: str, marker: str, occurrence: int = 1, keep: int = 0) -> str:
    """The text up to the `occurrence`-th line holding `marker`, plus `keep` lines of it."""
    lines = text.splitlines(keepends=True)
    seen = 0
    for n, line in enumerate(lines):
        if marker in line:
            seen += 1
            if seen == occurrence:
                return "".join(lines[: n + keep])
    raise AssertionError(f"{marker} not found {occurrence} times")


# ---------- xTB convergence criteria (D123b) ----------


def test_xtb_output_gives_the_last_cycle_criteria_to_the_final_structure():
    step = xtb.parse(read("xtb", "dvb_opt.out")).steps[0]
    assert len(step.geometries) == 1
    [criteria] = step.geometry_criteria
    assert [(c.name, c.value, c.threshold, c.converged, c.unit) for c in criteria] == [
        ("Energy change", -0.4673027e-05, 5e-06, True, "Eh"),
        ("Gradient norm", 0.0005969, 0.001, True, "Eh/bohr"),
    ]


def test_xtb_optimization_log_is_an_optimization_of_its_cycles(tmp_path):
    log = read("xtb", "dce_scan/xtbopt.log")
    assert xtb.is_optimization_log(log) and not xtb.is_optimization_log(
        read("xtb", "dce_scan/xtbscan.log")
    )
    # Alone: the thresholds of the normal level, and it says so.
    step = xtb.parse_optimization_log(log, "xtbopt.log").steps[0]
    assert step.job_type == "optimization" and len(step.geometries) == 7
    assert "convergence thresholds (taken as xTB's normal level" in step.missing[0]
    first, last = step.geometry_criteria[0], step.geometry_criteria[-1]
    assert [(c.name, c.value, c.converged) for c in first] == [
        ("Gradient norm", 0.258175264605, False)
    ]
    assert [c.name for c in last] == ["Energy change", "Gradient norm"]
    assert last[0].value == pytest.approx(step.geometry_energies[-1] - step.geometry_energies[-2])
    assert all(c.converged for c in last) and step.optimization_converged
    assert step.converged_geometries == [6] and step.termination == "normal"
    # With the output beside it: its thresholds and level of theory.
    beside = xtb.parse_optimization_log(log, "xtbopt.log", [read("xtb", "dce_scan/dce_scan.out")])
    assert beside.steps[0].missing == [] and beside.steps[0].route.method == "GFN2-xTB"
    # Still being written: up to its last whole structure, not converged.
    running = xtb.parse_optimization_log(_cut(log, "energy:", 5, keep=3), "xtbopt.log")
    assert len(running.steps[0].geometries) == 4
    assert running.steps[0].termination == "abnormal"


# ---------- reading a file (T-IMP-24) ----------


def test_a_finished_gaussian_job_shows_its_steps_structures_and_modes():
    found = inspection.read_path(FIXTURES / "gaussian" / "aminationTS-full-unfrz-c1.log")
    assert found.program == "Gaussian" and found.warnings == []
    opt, freq = found.steps
    assert opt.job_type == "ts_optimization" and opt.termination == "normal"
    assert len(opt.frames) == 10 and all(f.criteria for f in opt.frames)
    assert opt.optimization_converged and opt.charge is not None
    assert freq.job_type == "frequency" and freq.imaginary == 1
    assert freq.modes is not None and freq.frequencies[freq.modes.order[0]] < 0


@pytest.mark.parametrize(
    ("folder", "name", "marker", "occurrence"),
    [
        ("gaussian", "aziridinium-phos-full-c1.log", "Input orientation:", 20),
        ("gaussian", "aziridinium-phos-full-c1.log", "Maximum Force", 7),
        ("gaussian", "aminationTS-full-unfrz-c1.log", "Harmonic frequencies", 1),
        ("orca", "dvb_gopt.out", "GEOMETRY OPTIMIZATION CYCLE   3", 1),
        ("orca", "dvb_ir.out", "NORMAL MODES", 1),
        ("xtb", "dvb_opt.out", "CYCLE    4", 1),
    ],
)
def test_a_running_job_is_read_up_to_its_last_complete_block(folder, name, marker, occurrence):
    text = _cut(read(folder, name), marker, occurrence, keep=3)
    found = inspection.read(text, name)
    assert found.steps, found.warnings
    assert found.steps[-1].termination == "abnormal"
    assert any("has not ended normally" in w for w in found.warnings)


@pytest.mark.parametrize(
    ("folder", "name"),
    [("orca", "dvb_ir.out"), ("gaussian", "dvb_scan_unrelaxed.log"), ("xtb", "dvb_ir.out")],
)
def test_every_cut_of_an_output_is_read_or_refused_with_a_reason(folder, name):
    text = read(folder, name)
    for size in range(200, len(text), max(len(text) // 150, 1)):
        try:
            inspection.read(text[:size], name)
        except inspection.InspectFailed as exc:
            assert str(exc)


def test_a_cut_the_parser_trips_over_is_read_without_its_last_lines(monkeypatch):
    text = read("gaussian", "aziridinium-phos-full-c1.log")
    real = inspection.imports.read_file

    def tripping(given, name="", folder=None):
        if given == text:
            raise IndexError("the parser met the cut")
        return real(given, name, folder)

    monkeypatch.setattr(inspection.imports, "read_file", tripping)
    found = inspection.read(text, "aziridinium-phos-full-c1.log")
    assert any("the last 1 lines are left out" in w for w in found.warnings)
    assert len(found.steps) == 2


def test_xyz_files_open_too(tmp_path):
    ensemble = inspection.read_path(FIXTURES / "crest" / "crest_conformers.xyz")
    assert ensemble.program == "CREST" and ensemble.steps[0].job_type == "structures"
    assert len(ensemble.steps[0].frames) == 74
    assert ensemble.steps[0].frames[0].energy is not None
    one = tmp_path / "water.xyz"
    one.write_text("3\nwater\nO 0 0 0.117\nH 0 0.757 -0.467\nH 0 -0.757 -0.467\n")
    found = inspection.read_path(one)
    assert found.program == "XYZ" and len(found.steps[0].frames) == 1
    being_written = tmp_path / "movie.xyz"
    frames = [f"3\nstep {n}\nO 0 0 0.1{n}\nH 0 0.757 -0.467\nH 0 -0.757 -0.467\n" for n in range(3)]
    being_written.write_text("".join(frames) + "3\nstep 4\nO 0 0 0.1\n")
    found = inspection.read_path(being_written)
    assert len(found.steps[0].frames) == 3
    assert "The file ends inside structure 4" in found.warnings[0]
    log = tmp_path / "xtbopt.log"
    shutil.copy(FIXTURES / "xtb" / "dce_scan" / "xtbopt.log", log)
    found = inspection.read_path(log)
    assert found.program == "xTB" and found.steps[0].job_type == "optimization"
    assert [len(f.criteria) for f in found.steps[0].frames] == [1] + [2] * 6
    # Beside its output, the output's thresholds are used.
    shutil.copy(FIXTURES / "xtb" / "dce_scan" / "dce_scan.out", tmp_path / "dce_scan.out")
    assert inspection.read_path(log).steps[0].missing == []


def test_what_cannot_be_inspected(tmp_path):
    text = tmp_path / "job.gjf"
    text.write_text("#P B3LYP/6-31G(d) Opt\n\njob\n\n0 1\n")
    with pytest.raises(inspection.InspectFailed, match="is not a Gaussian, ORCA or xTB"):
        inspection.read_path(text)
    binary = tmp_path / "job.chk"
    binary.write_bytes(b"\x00\x01" * 100)
    with pytest.raises(inspection.InspectFailed, match="not a text file"):
        inspection.read_path(binary)


# ---------- the API, for a file on this computer ----------


def test_a_local_file_is_inspected_refreshed_and_imported(page, tmp_path):
    path = tmp_path / "running.log"
    full = AZIRIDINIUM.read_text(encoding="utf-8")
    path.write_text(_cut(full, "Input orientation:", 10, keep=3), encoding="utf-8")
    response = page.post("/api/inspections", json={"path": str(path)}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    shown = response.json()
    assert (shown["name"], shown["path"], shown["server"]) == ("running.log", str(path), None)
    assert shown["program"] == "Gaussian" and len(shown["steps"]) == 1
    frames = shown["steps"][0]["frames"]
    assert len(frames) == 9 and frames[0]["criteria"][0]["name"] == "Maximum force"
    assert shown["steps"][0]["termination"] == "abnormal"

    # The job goes on: Refresh reads it again.
    path.write_text(full, encoding="utf-8")
    response = page.post(f"/api/inspections/{shown['id']}/refresh", headers=ORIGIN)
    again = response.json()
    assert again["id"] == shown["id"] and len(again["steps"]) == 2
    assert again["steps"][1]["modes"] is not None and again["warnings"] == []

    # Import needs an investigation; it then goes on with the import preview.
    response = page.post(f"/api/inspections/{shown['id']}/import", json={}, headers=ORIGIN)
    assert response.status_code == 409
    page.post("/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Ru"})
    response = page.post(f"/api/inspections/{shown['id']}/import", json={}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["origin"]["path"] == str(path) and len(plan["steps"]) == 2
    # Nothing was written to the investigation.
    assert page.get("/api/canvas").json()["nodes"] == []

    assert page.delete(f"/api/inspections/{shown['id']}", headers=ORIGIN).status_code == 204
    response = page.post(f"/api/inspections/{shown['id']}/refresh", headers=ORIGIN)
    assert response.status_code == 404


def test_inspect_refusals(page, tmp_path):
    response = page.post("/api/inspections", json={"path": str(tmp_path / "missing.log")})
    assert response.status_code == 403  # not the app's own page
    response = page.post(
        "/api/inspections", json={"path": str(tmp_path / "missing.log")}, headers=ORIGIN
    )
    assert response.status_code == 422 and "is not a file" in response.json()["detail"]
    response = page.post(
        "/api/inspections", json={"path": "/x.log", "server": "nowhere"}, headers=ORIGIN
    )
    assert response.status_code == 404
