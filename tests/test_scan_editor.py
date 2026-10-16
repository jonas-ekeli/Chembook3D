"""The scan editor (D135, FR-3D-14, T-IMP-35): the coordinate a scan drives, scans put on a path
the right way round, the checks at each join, and the saved file read back."""

import copy
import dataclasses

import pytest
from fastapi.testclient import TestClient

from chembook3d.app import create_app
from chembook3d.parsers import gaussian, orca
from chembook3d.services import inspection
from chembook3d.services import scan_editor as editor
from tests.test_orca_xtb_parsers import FIXTURES, read

APP = "http://127.0.0.1:8765"
ORIGIN = {"origin": APP}
RELAXED = FIXTURES / "gaussian" / "dvb_scan_relaxed.log"


@pytest.fixture
def page():
    with TestClient(create_app(), base_url=APP, client=("127.0.0.1", 50000)) as test_client:
        yield test_client


def _scans(path, key="f") -> list[editor.Scan]:
    text = inspection.read_text_of(path)
    parsed, _ = inspection.parse_text(text, path.name, path.parent)
    return editor.scans_of(parsed, path.name, str(path), key)


def _part(scan: editor.Scan, key: str, indexes: list[int], name: str) -> editor.Scan:
    """A scan made of some of `scan`'s points, as if another file had scanned them."""
    part = dataclasses.replace(scan, key=key, file=name, points=[])
    part.points = [copy.deepcopy(scan.points[k]) for k in indexes]
    for n, point in enumerate(part.points, start=1):
        point.number = n
    return part


@pytest.fixture
def relaxed() -> editor.Scan:
    [scan] = _scans(RELAXED)
    return scan


# ---------- the scanned coordinate ----------


def test_the_coordinate_a_gaussian_scan_names(relaxed):
    assert relaxed.atoms == [3, 4, 9, 10] and relaxed.found_by == "file"
    assert relaxed.coordinate == "dihedral C3–C4–C9–C10" and relaxed.unit == "°"
    values = [p.value for p in relaxed.points]
    assert values[0] == pytest.approx(180.0, abs=0.05)
    assert all(
        b - a == pytest.approx(30.0, abs=0.1) for a, b in zip(values, values[1:], strict=False)
    )
    assert len(relaxed.points) == 13 and relaxed.left_out == 0
    assert relaxed.level_label == "Gaussian B3LYP/STO-3G" and relaxed.basis_named


def test_the_input_line_names_it_when_the_parameter_table_does_not():
    text = read("gaussian", "dvb_scan_relaxed.log")
    without = "".join(
        line for line in text.splitlines(keepends=True) if "Scan    " not in line or "!" not in line
    )
    [step] = gaussian.parse(without).steps
    assert step.scan_coordinates == [[3, 4, 9, 10]]
    for line, atoms in [
        (" B       1       2 S  10 0.1000", [1, 2]),
        (" 1 2 S 10 -0.05", [1, 2]),
        (" A 1 2 3 S 5 2.0", [1, 2, 3]),
    ]:
        match = gaussian._SCAN_LINE_RE.match(line)
        assert match and [int(a) for a in match.group(1).split()] == atoms


def test_the_coordinate_an_orca_scan_block_names():
    echoed = ["! b3lyp def2-svp opt", "%geom scan", "b 0 1 = 1.35, 1.10, 12", "end", "end"]
    assert orca._scan_coordinates(echoed) == [[1, 2]]
    echoed = ["%geom", "scan", "d 4 0 1 2 [10.0 20.0 30.0]", "a 0 1 2 = 100, 120, 5", "end"]
    assert orca._scan_coordinates(echoed) == [[5, 1, 2, 3], [1, 2, 3]]


def test_a_coordinate_found_from_the_structures():
    # A rigid Z-matrix scan and xTB's scan log name no atoms; the dihedral is found.
    [rigid] = _scans(FIXTURES / "orca" / "dvb_scan_unrelaxed.out")
    assert rigid.found_by == "structures" and rigid.atoms == [3, 4, 9, 10]
    [xtb] = _scans(FIXTURES / "xtb" / "dce_scan" / "xtbscan.log")
    assert xtb.found_by == "structures" and len(xtb.atoms) == 4 and len(xtb.points) == 13
    assert xtb.level_label == "xTB GFN2-xTB"


# ---------- putting scans on the path ----------


def test_two_directions_from_a_ts_are_joined_through_it(relaxed):
    # The scan runs round 360°; its top is point 4 (270°).
    up = _part(relaxed, "up:1", [3, 4, 5, 6], "up.log")
    down = _part(relaxed, "down:1", [3, 2, 1, 0], "down.log")
    scans = {s.key: s for s in (up, down)}
    pieces, notes = editor.chain(scans, ["up:1", "down:1"], [])
    # Neither continues the other, so the first starts the path and the second, which starts
    # from the same structure, is turned round and put in front.
    assert pieces == [editor.Piece("down:1", 3, 0), editor.Piece("up:1", 0, 3)]
    assert "turned round and put in front" in notes[1]
    joined = editor.join(scans, pieces)
    assert joined.blocked is None and len(joined.frames) == 7  # the TS kept once
    values = [p.value for p in relaxed.points[:7]]
    assert [f.x for f in joined.frames] == pytest.approx(values, abs=0.01)
    [shared] = joined.joins
    assert shared.shared and shared.ok and shared.energy_difference == pytest.approx(0, abs=1e-6)
    assert [m.kind for m in joined.marks] == ["shared"] and not joined.marks[0].warning
    assert joined.frames[0].source == "down.log, step 1, point 4 (reversed)"
    energies = [p.energy for p in relaxed.points]
    assert joined.top == 3
    assert joined.barrier == pytest.approx((energies[3] - energies[0]) * editor.KCAL)


def test_a_continuation_is_chained_and_a_scan_back_over_the_path_is_not(relaxed):
    first = _part(relaxed, "a:1", [0, 1, 2], "a.log")
    second = _part(relaxed, "b:1", [2, 3, 4, 5], "b.log")
    back = _part(relaxed, "c:1", [4, 3, 2], "finer.log")  # runs back over the path
    scans = {s.key: s for s in (first, second, back)}
    pieces, notes = editor.chain(scans, ["c:1", "b:1", "a:1"], [])
    assert pieces == [editor.Piece("a:1", 0, 2), editor.Piece("b:1", 0, 3)]
    assert notes[-1] == "finer.log does not go on from either end of the path; it is in the list."


def test_place_turns_a_scan_to_go_on_from_the_end(relaxed):
    first = _part(relaxed, "a:1", [0, 1, 2, 3], "a.log")
    later = _part(relaxed, "b:1", [6, 5, 4, 3], "b.log")
    scans = {s.key: s for s in (first, later)}
    pieces, note = editor.place(scans, [editor.Piece("a:1", 0, 3)], "b:1")
    assert pieces[-1] == editor.Piece("b:1", 3, 0) and "turned round" in note


# ---------- checks at each join ----------


def test_pieces_at_different_levels_are_not_joined(relaxed):
    other = _part(relaxed, "b:1", list(range(6, 13)), "b.log")
    other.level = {**other.level, "method": "PBE0"}
    other.level_label = "PBE0/STO-3G"
    scans = {"f:1": relaxed, "b:1": other}
    joined = editor.join(scans, [editor.Piece("f:1", 0, 6), editor.Piece("b:1", 0, 6)])
    assert joined.frames == [] and "the method differ" in joined.blocked
    # A basis set read from the checkpoint is not compared, and the path says so.
    other.level = dict(relaxed.level, basis="")
    other.basis_named = False
    joined = editor.join(scans, [editor.Piece("f:1", 0, 6), editor.Piece("b:1", 0, 6)])
    assert joined.blocked is None and "ChkBasis" in joined.notes[0]


def test_other_atoms_are_not_joined(relaxed):
    other = _part(relaxed, "b:1", list(range(6, 13)), "b.log")
    for point in other.points:
        point.rows = [point.rows[1], point.rows[0], *point.rows[2:]]
    other.points[0].rows[0] = ["H", *other.points[0].rows[0][1:]]
    joined = editor.join(
        {"f:1": relaxed, "b:1": other}, [editor.Piece("f:1", 0, 6), editor.Piece("b:1", 0, 6)]
    )
    assert "other atoms" in joined.blocked


def test_a_shared_structure_with_other_energies_is_marked(relaxed):
    other = _part(relaxed, "b:1", list(range(6, 13)), "b.log")
    for point in other.points:
        point.energy += 0.001  # 0.63 kcal/mol
    joined = editor.join(
        {"f:1": relaxed, "b:1": other}, [editor.Piece("f:1", 0, 6), editor.Piece("b:1", 0, 6)]
    )
    [join] = joined.joins
    assert join.shared and not join.ok and join.energy_difference == pytest.approx(0.6275, abs=1e-3)
    assert any(m.kind == "energies" and m.warning for m in joined.marks)


def test_a_jump_an_energy_step_and_turning_back_are_marked(relaxed):
    scans = {"f:1": relaxed}
    # Points 0-4 then 9-12: a jump of 150° in the dihedral.
    joined = editor.join(scans, [editor.Piece("f:1", 0, 4), editor.Piece("f:1", 9, 12)])
    [join] = joined.joins
    assert not join.shared and not join.ok and join.rmsd > editor.JUMP
    assert {m.kind for m in joined.marks} >= {"join", "jump"}
    # An energy that drops in one step far more than the steps beside it.
    stepped = _part(relaxed, "s:1", list(range(13)), "s.log")
    for point in stepped.points[5:]:
        point.energy -= 0.02  # 12.6 kcal/mol
    joined = editor.join({"s:1": stepped}, [editor.Piece("s:1", 0, 12)])
    [step] = [m for m in joined.marks if m.kind == "step"]
    assert step.at == 5 and "drops 1" in step.text
    # Forward then back again.
    joined = editor.join(scans, [editor.Piece("f:1", 0, 6), editor.Piece("f:1", 5, 3)])
    [back] = [m for m in joined.marks if m.kind == "back"]
    assert back.at == 7


def test_pieces_on_other_coordinates_go_along_the_path_length(relaxed):
    other = _part(relaxed, "b:1", list(range(6, 13)), "b.log")
    other.atoms = [1, 2]
    joined = editor.join(
        {"f:1": relaxed, "b:1": other}, [editor.Piece("f:1", 0, 6), editor.Piece("b:1", 0, 6)]
    )
    assert joined.axis == "path" and joined.unit == "amu½·bohr"
    xs = [f.x for f in joined.frames]
    assert xs[0] == 0 and all(b > a for a, b in zip(xs, xs[1:], strict=False))


def test_pieces_that_run_past_their_scan_are_refused(relaxed):
    with pytest.raises(editor.ScanEditorError, match="runs past its 13 points"):
        editor.join({"f:1": relaxed}, [editor.Piece("f:1", 0, 13)])


# ---------- the saved file ----------


def test_the_saved_file_is_read_back_as_one_scan(relaxed):
    down = _part(relaxed, "down:1", [3, 2, 1, 0], "down.log")
    up = _part(relaxed, "up:1", list(range(3, 13)), "up.log")
    joined = editor.join(
        {"down:1": down, "up:1": up}, [editor.Piece("down:1", 3, 0), editor.Piece("up:1", 0, 9)]
    )
    text = joined.text
    lines = text.splitlines()
    assert lines[0] == str(len(relaxed.points[0].rows))
    assert lines[1].startswith("chembook3d joined scan 1 | program: Gaussian |")
    assert "| level: Gaussian B3LYP/STO-3G |" in lines[1] and "| coordinate: 3 4 9 10 |" in lines[1]
    assert editor.is_joined_scan(text) and not editor.is_joined_scan(
        read("xtb", "dce_scan/xtbscan.log")
    )
    [back] = editor.read_joined(text, "joined.xyz", "/x/joined.xyz", "j")
    assert back.joined and back.atoms == [3, 4, 9, 10] and len(back.points) == 13
    assert back.level == relaxed.level and back.level_label == "Gaussian B3LYP/STO-3G"
    assert [p.energy for p in back.points] == pytest.approx([p.energy for p in relaxed.points])
    assert back.points[0].source == "down.log, step 1, point 4 (reversed)"
    # Read back, it joins the scans it came from: same level, and continues them.
    pieces, _ = editor.chain({"j:1": back, "f:1": relaxed}, ["j:1"], [])
    assert editor.join({"j:1": back, "f:1": relaxed}, pieces).blocked is None
    assert editor.level_difference(back, relaxed) == []


# ---------- the API ----------


def test_files_are_read_and_the_path_is_joined(page, tmp_path):
    response = page.post(
        "/api/scan-editor",
        json={"targets": [{"path": str(RELAXED)}, {"path": str(tmp_path / "none.log")}]},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    shown = response.json()
    [scan] = shown["scans"]
    assert shown["added"] == [scan["key"]] and scan["coordinate"] == "dihedral C3–C4–C9–C10"
    assert len(scan["points"]) == 13 and scan["level"] == "Gaussian B3LYP/STO-3G"
    [problem] = shown["problems"]
    assert "is not a file" in problem["problem"]

    # A file with no scan says so; the editor keeps what it had.
    response = page.post(
        f"/api/scan-editor/{shown['id']}/files",
        json={"targets": [{"path": str(FIXTURES / "orca" / "dvb_gopt.out")}]},
        headers=ORIGIN,
    )
    assert "holds no scan" in response.json()["problems"][0]["problem"]
    assert len(response.json()["scans"]) == 1

    key = scan["key"]
    response = page.post(
        f"/api/scan-editor/{shown['id']}/path", json={"pieces": [], "add": key}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    path = response.json()
    assert path["pieces"] == [{"scan": key, "first": 0, "last": 12}]
    assert len(path["frames"]) == 13 and path["axis"] == "coordinate" and path["unit"] == "°"
    assert path["text"].splitlines()[1].startswith("chembook3d joined scan 1")

    # Reversed, the path runs the other way.
    response = page.post(
        f"/api/scan-editor/{shown['id']}/path",
        json={"pieces": [{"scan": key, "first": 12, "last": 0}]},
        headers=ORIGIN,
    )
    assert response.json()["frames"][0]["x"] == pytest.approx(path["frames"][-1]["x"], abs=0.01)
    response = page.post(
        f"/api/scan-editor/{shown['id']}/path",
        json={"pieces": [{"scan": key, "first": 0, "last": 20}]},
        headers=ORIGIN,
    )
    assert response.status_code == 422

    assert (
        page.delete(f"/api/scan-editor/{shown['id']}/scans/{key}", headers=ORIGIN).status_code
        == 204
    )
    response = page.post(
        f"/api/scan-editor/{shown['id']}/path", json={"pieces": [], "add": key}, headers=ORIGIN
    )
    assert response.status_code == 422
    assert page.delete(f"/api/scan-editor/{shown['id']}", headers=ORIGIN).status_code == 204
    response = page.post(
        f"/api/scan-editor/{shown['id']}/path", json={"pieces": []}, headers=ORIGIN
    )
    assert response.status_code == 404


def test_a_saved_joined_scan_opens_again(page, tmp_path):
    [scan] = _scans(RELAXED)
    joined = editor.join({scan.key: scan}, [editor.Piece(scan.key, 12, 0)])
    saved = tmp_path / "joined.xyz"
    saved.write_text(joined.text, encoding="utf-8")
    response = page.post(
        "/api/scan-editor", json={"targets": [{"path": str(saved)}]}, headers=ORIGIN
    )
    [back] = response.json()["scans"]
    assert back["joined"] and back["program"] == "Gaussian" and len(back["points"]) == 13
    assert back["points"][0]["source"] == "dvb_scan_relaxed.log, step 1, point 13 (reversed)"


def test_the_scan_editor_is_the_page_s_own(page):
    assert page.post("/api/scan-editor", json={"targets": []}).status_code == 403
    response = page.post(
        "/api/scan-editor",
        json={"targets": [{"path": "/x.log", "server": "nowhere"}]},
        headers=ORIGIN,
    )
    assert response.status_code == 404


def test_an_imported_scan_opens_from_its_stored_copy(page, tmp_path):
    from tests.test_step_movie import import_file

    page.post("/api/investigations", json={"folder": str(tmp_path / "inv"), "name": "Ru"})
    calculation = import_file(page, "gaussian", "dvb_scan_relaxed.log")
    response = page.post(
        "/api/scan-editor",
        json={"targets": [{"calculation": calculation["id"]}, {"calculation": "nothing"}]},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    [scan] = response.json()["scans"]
    assert scan["file"] == "dvb_scan_relaxed.log" and len(scan["points"]) == 13
    assert "not imported from a file" in response.json()["problems"][0]["problem"]
    # Closing the editor leaves the investigation's copy alone.
    page.delete(f"/api/scan-editor/{response.json()['id']}", headers=ORIGIN)
    assert page.get(f"/api/calculations/{calculation['id']}/steps").status_code == 200
