"""H, G and G_qh from MACE-OMOL-0 frequencies are marked approximate (D245, FR-EN-29, T-EN-56).

A local result (the worker's `result.json`, D213) is imported as written by a MACE-OMOL-0 job
with frequencies (D217), then a Psi4 single point at its structure (D240), so G_qh exists at the
MACE level and at "Psi4 … // MACE MACE-OMOL-0". Their checks carry W-ML-THERMO; the value is
the same as with the note left out (D19).
"""

import json

from chembook3d.services import energies
from tests import gaussian_text as g
from tests.test_energies import import_file, view
from tests.test_pathway import edge, get, patch, post

MACE = "MACE MACE-OMOL-0"
COMPOSITE = f"Psi4 PBE-D3M(BJ)/def2-TZVP // {MACE}"
NOTE = (
    "approximate: the thermal correction is from MACE-OMOL-0 frequencies (2.5 kcal/mol off "
    "DFT's on a tested Ru TS and metallacyclobutane pair)"
)


def _frame(shift: float) -> list[list[float]]:
    return [[x, y, z] for _, _, x, y, z in g.moved(g.WATER, shift)]


def mace_result(shift: float, wavenumbers) -> str:
    """A MACE-OMOL-0 optimization with frequencies, as the worker writes it."""
    return json.dumps(
        {
            "format": "chembook3d-local-result",
            "method": "mace-omol",
            "program": "MACE",
            "level": "MACE-OMOL-0",
            "charge": 0,
            "multiplicity": 1,
            "state": "converged",
            "elements": [a[0] for a in g.WATER],
            "frames": [_frame(shift)],
            "energies": [-76.30],
            "energy": -76.30,
            "frequencies": {"wavenumbers": list(wavenumbers), "energy": -76.30},
        }
    )


def psi4_single_point(shift: float) -> str:
    return json.dumps(
        {
            "format": "chembook3d-local-result",
            "method": "psi4-sp",
            "program": "Psi4",
            "level": "single point",
            "charge": 0,
            "multiplicity": 1,
            "state": "converged",
            "elements": [a[0] for a in g.WATER],
            "coordinates": _frame(shift),
            "energy": -76.42,
            "single_point": {"method": "PBE", "dispersion": "D3M(BJ)", "basis_name": "def2-TZVP"},
        }
    )


def keys(client) -> dict[str, str]:
    return {o["label"]: o["key"] for o in get(client, "/energies/options")["levels"]}


def mace_node(client, label: str, shift: float, wavenumbers, role: str) -> str:
    node_id = import_file(client, f"{label}.json", mace_result(shift, wavenumbers))["node_id"]
    import_file(client, f"{label}-sp.json", psi4_single_point(shift), node_id)
    patch(client, f"/nodes/{node_id}", {"label": label, "role": role})
    return node_id


def test_values_from_mace_frequencies_are_marked_approximate(open_client, monkeypatch):
    minimum = mace_node(open_client, "MCB", 0.0, (1650.0, 3700.0, 3800.0), "minimum")
    ts = mace_node(open_client, "TS", 0.05, (1650.0, 3700.0, 3800.0), "transition_state")
    dft = import_file(open_client, "dft.out", g.opt_freq(g.WATER, g.moved(g.WATER, 0.1)))
    found = keys(open_client)
    assert COMPOSITE in found and MACE in found

    seen = {}
    for label in (COMPOSITE, MACE):
        for energy_type in ("E", "H", "G", "G_qh"):
            values = view(open_client, found[label], energy_type)["values"]
            seen[label, energy_type] = values
            check = values[minimum]["check"]
            if energy_type in ("E", "H", "G"):
                # E is not from the frequencies; MACE gives no H or G (D217), and an n/a
                # value gets no note.
                assert check is None
            else:
                assert check == {
                    "code": "W-ML-THERMO",
                    "message": NOTE,
                    "issues": [{"code": "W-ML-THERMO", "message": NOTE}],
                }
            # A TS without its imaginary frequency keeps that warning first.
            issues = values[ts]["check"]["issues"]
            assert issues[0]["code"] == "W-IMAG-TS"
            noted = ["W-ML-THERMO"] if energy_type == "G_qh" else []
            assert [i["code"] for i in issues[1:]] == noted
    assert seen[COMPOSITE, "G_qh"][minimum]["value"] is not None
    assert seen[MACE, "G_qh"][minimum]["value"] is not None

    # A Gaussian frequency job's values have no such note.
    gaussian = next(label for label in found if label.startswith("Gaussian"))
    values = view(open_client, found[gaussian], "G")["values"]
    assert values[dft["node_id"]]["check"] is None

    # The value is the one it has without the note (D19).
    monkeypatch.setattr(energies, "MACHINE_LEARNED_THERMO", {})
    for (label, energy_type), values in seen.items():
        plain = view(open_client, found[label], energy_type)["values"]
        assert plain[minimum]["check"] is None
        assert plain[minimum]["value"] == values[minimum]["value"]


def test_the_note_reaches_profiles_and_the_table(open_client):
    minimum = mace_node(open_client, "MCB", 0.0, (1650.0, 3700.0, 3800.0), "minimum")
    ts = mace_node(open_client, "TS", 0.05, (-200.0, 1650.0, 3700.0), "transition_state")
    edge(open_client, minimum, ts)
    body = {"paths": [[minimum, ts]], "level": keys(open_client)[COMPOSITE], "type": "G_qh"}
    profile = post(open_client, "/energies/profile", body, status=200)
    points = profile["profiles"][0]["points"]
    assert [p["check"]["code"] for p in points] == ["W-ML-THERMO", "W-ML-THERMO"]
    assert points[1]["relative"] is not None
    table = post(open_client, "/energies/table", body, status=200)
    assert [row[-1] for row in table["rows"]] == [NOTE, NOTE]
