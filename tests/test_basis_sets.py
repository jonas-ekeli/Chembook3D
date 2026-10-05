"""Saved custom basis sets: inspection and the Gaussian basis file (D94)."""

import re

from chembook3d.parsers import gaussian
from chembook3d.services import basis_sets
from chembook3d.xyz import Atom
from tests import gaussian_text as g
from tests.test_import import commit, named, upload

RULE = " " + "=" * 102
# A Pseudopotential Parameters table as Gaussian 16 prints it (with the spin-orbit column):
# carbon without an ECP, ruthenium with a 28-electron core and blocks up to G.
RU_ECP = [
    "                                       Pseudopotential Parameters",
    RULE,
    "  Center     Atomic      Valence      Angular      Power",
    "  Number     Number     Electrons     Momentum     of R      Exponent        Coefficient"
    "   SO-Coeffient",
    RULE,
    "    1          6",
    "                                   No pseudopotential on this center.",
    "    2         44           16",
    "                                      G and up ",
    "                                                     2        1.0000000        0.00000000"
    "    0.00000000",
    "                                      S - G",
    "                                                     2       11.0000000      200.00000000"
    "    0.00000000",
    "                                                     2        5.0000000       30.00000000"
    "    0.00000000",
    "                                      P - G",
    "                                                     2       10.0000000       50.00000000"
    "    0.00000000",
    "                                      D - G",
    "                                                     2        9.0000000       25.00000000"
    "    0.00000000",
    "                                      F - G",
    "                                                     2        8.0000000       -9.00000000"
    "    0.00000000",
    RULE,
]
RU_BASIS = [
    " General basis read from cards:  (5D, 7F)",
    " Centers:       1",
    " SP 2 1.000",
    "     Exponent=  3.0000000000D+00 Coefficients=  4.0000000000D-01  2.0000000000D-01",
    "     Exponent=  6.0000000000D-01 Coefficients=  6.0000000000D-01  8.0000000000D-01",
    " ****",
    " Centers:       2",
    " S 3 1.000",
    "     Exponent=  1.5000000000D+02 Coefficients=  1.0000000000D-03",
    "     Exponent=  2.0000000000D+01 Coefficients= -4.0000000000D-02",
    "     Exponent=  1.1000000000D+00 Coefficients=  8.0000000000D-01",
    " S 1 1.000",
    "     Exponent=  1.2000000000D-01 Coefficients=  1.0000000000D+00",
    " D 2 1.000",
    "     Exponent=  2.5000000000D+00 Coefficients=  5.0000000000D-01",
    "     Exponent=  7.0000000000D-01 Coefficients=  5.0000000000D-01",
    " ****",
]


def ru_definition() -> dict[str, str]:
    lines = RU_BASIS + RU_ECP
    basis = gaussian._read_general_basis(lines, 0)
    ecp = gaussian._read_pseudopotentials(lines, len(RU_BASIS))
    atoms = [Atom("C", 0, 0, 0), Atom("Ru", 0, 0, 2)]
    return gaussian._element_basis(atoms, basis, ecp)


def read_gbs(text: str) -> tuple[dict, dict]:
    """A small reader of the written file: the basis blocks, then the ECP blocks."""
    lines = [line for line in text.splitlines() if not line.startswith("!")]
    blank = lines.index("") if "" in lines else len(lines)
    bases: dict[str, list] = {}
    i = 0
    while i < blank:
        element = lines[i].split()[0]
        shells = []
        i += 1
        while lines[i] != "****":
            kind, count, scale = lines[i].split()
            primitives = [lines[i + 1 + k].split() for k in range(int(count))]
            shells.append((kind, scale, primitives))
            i += 1 + int(count)
        bases[element] = shells
        i += 1
    ecps: dict[str, tuple] = {}
    i = blank + 1
    while i < len(lines):
        element = lines[i].split()[0]
        _, lmax, core = lines[i + 1].split()
        i += 2
        blocks = []
        while i < len(lines) and not re.match(r"^[A-Z][a-z]?\s+0$", lines[i]):
            title, count = lines[i], int(lines[i + 1])
            blocks.append((title, [lines[i + 2 + k].split() for k in range(count)]))
            i += 2 + count
        ecps[element] = (int(lmax), int(core), blocks)
    return bases, ecps


def test_the_file_gives_back_every_printed_number():
    definition = ru_definition()
    text = basis_sets.gaussian_file("modRu", definition)
    assert text.startswith("! Custom basis set modRu, saved by Chembook3D\n")
    assert "! Use with GenECP\n" in text and "! Gaussian read it as (5D, 7F)\n" in text
    bases, ecps = read_gbs(text)
    assert list(bases) == ["C", "Ru"]  # periodic-table order
    assert bases["C"] == [
        ("SP", "1.000", [["3.0000000000D+00", "4.0000000000D-01", "2.0000000000D-01"],
                         ["6.0000000000D-01", "6.0000000000D-01", "8.0000000000D-01"]]),
    ]  # fmt: skip
    printed = [line.split() for line in RU_BASIS[7:-1] if "Exponent=" in line]
    written = [p for _, _, primitives in bases["Ru"] for p in primitives]
    assert written == [[w[1], w[3]] for w in printed]
    assert [(k, len(p)) for k, _, p in bases["Ru"]] == [("S", 3), ("S", 1), ("D", 2)]

    lmax, core, blocks = ecps["Ru"]
    assert (lmax, core) == (4, 28) and "C" not in ecps
    assert [t for t, _ in blocks] == ["G and up", "S - G", "P - G", "D - G", "F - G"]
    assert blocks[1][1] == [["2", "11.0000000", "200.00000000"], ["2", "5.0000000", "30.00000000"]]
    assert blocks[4][1] == [["2", "8.0000000", "-9.00000000"]]


def test_contraction_and_ecp_summary():
    described = {e["element"]: e for e in basis_sets.describe(ru_definition())}
    ru = described["Ru"]["variants"][0]
    assert ru["contraction"] == "(4s2d)/[2s1d]"
    assert ru["ecp"]["core_electrons"] == 28 and ru["ecp"]["max_angular"] == 4
    assert ru["header"] == "(5D, 7F)"
    carbon = described["C"]["variants"][0]
    assert carbon["contraction"] == "(2s2p)/[1s1p]" and carbon["ecp"] is None
    assert carbon["text"][0] == "C     0" and carbon["text"][-1] == "****"


def test_only_chosen_elements_and_no_ecp_section_without_ecps():
    text = basis_sets.gaussian_file("modRu", ru_definition(), ["C"])
    assert "! Use with Gen\n" in text and "Ru" not in text.split("! Use with")[1]
    assert "" not in text.splitlines()  # no ECP section


def test_atoms_of_one_element_with_different_definitions():
    lines = RU_BASIS + RU_ECP
    basis = gaussian._read_general_basis(lines, 0)
    atoms = [Atom("C", 0, 0, 0), Atom("C", 0, 0, 2)]  # carbon on centre 2 gets the Ru set
    definition = gaussian._element_basis(atoms, basis, {})
    assert len(basis_sets.describe(definition)[0]["variants"]) == 2
    text = basis_sets.gaussian_file("mixed", definition)
    assert "! C: the first file gave its atoms 2 different definitions" in text
    assert text.count("****") == 1


def test_inspect_and_download_through_the_api(open_client):
    plan = upload(open_client, "MeI_TS.out", g.custom_chain(ts=True))
    node_id = commit(open_client, plan, **named(plan))["node_id"]

    listed = open_client.get("/api/custom-bases").json()
    assert [(b["name"], b["elements"]) for b in listed] == [("modDZ", ["H", "C", "I"])]
    calculations = open_client.get(f"/api/nodes/{node_id}/calculations").json()
    assert listed[0]["calculation_count"] == len(calculations) > 0

    detail = open_client.get(f"/api/custom-bases/{listed[0]['id']}").json()
    assert [e["element"] for e in detail["elements"]] == ["H", "C", "I"]
    iodine = detail["elements"][2]["variants"][0]
    assert iodine["contraction"] == "(3s2p)/[1s1p]" and iodine["ecp"]["core_electrons"] == 28
    assert {c["node_id"] for c in detail["calculations"]} == {node_id}

    response = open_client.get(f"/api/custom-bases/{listed[0]['id']}/file")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="modDZ.gbs"'
    bases, ecps = read_gbs(response.text)
    assert list(bases) == ["H", "C", "I"] and list(ecps) == ["I"]
    # the synthetic printout has only the "F and up" block, which the file points out
    assert "! I: Gaussian printed 1 ECP block for L up to 3" in response.text
    # the iodine exponents of the printed block (tests/gaussian_text.basis_block)
    expected = [f"{e * 53:.10E}".replace("E", "D") for e in (40.0, 6.0, 1.2, 3.0, 0.6)]
    assert [p[0] for _, _, ps in bases["I"] for p in ps] == expected

    only = open_client.get(f"/api/custom-bases/{listed[0]['id']}/file?elements=C,H")
    assert re.search(r"^! Elements: H C$", only.text, re.M)
    wrong = open_client.get(f"/api/custom-bases/{listed[0]['id']}/file?elements=Fe")
    assert wrong.status_code == 422 and "Fe" in wrong.json()["detail"]
    assert open_client.get("/api/custom-bases/nope").status_code == 404
