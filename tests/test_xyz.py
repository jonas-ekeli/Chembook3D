import pytest

from chembook3d.xyz import Atom, XyzParseError, format_xyz, formula, parse_xyz
from tests.conftest import WATER


def test_parses_standard_file_with_count_and_comment():
    atoms = parse_xyz(WATER)
    assert [a.element for a in atoms] == ["O", "H", "H"]
    assert atoms[1] == Atom("H", 0.0, 0.7572, -0.4692)


def test_parses_bare_atom_lines_lowercase_and_atomic_numbers():
    atoms = parse_xyz("ru 0 0 0\n6 1.9 0 0\n\n")
    assert [a.element for a in atoms] == ["Ru", "C"]


def test_comment_line_is_skipped_even_if_it_looks_like_an_atom():
    atoms = parse_xyz("1\nH 9 9 9\nHe 0 0 0\n")
    assert atoms == [Atom("He", 0.0, 0.0, 0.0)]


def test_invalid_line_is_reported_with_its_line_number():
    # T-ID-07
    with pytest.raises(XyzParseError) as exc:
        parse_xyz("O 0 0 0\nRu 0.0 abc 1.0\nXx 0 0 0\nH 1 2\n")
    assert [(e.line, e.message) for e in exc.value.errors] == [
        (2, "coordinates must be numbers"),
        (3, "unknown element 'Xx'"),
        (4, "expected 4 fields (element x y z), found 3"),
    ]


def test_count_mismatch_is_an_error():
    with pytest.raises(XyzParseError, match="count line says 4 atoms, but 3"):
        parse_xyz(WATER.replace("3\n", "4\n", 1))


def test_empty_text_is_an_error():
    with pytest.raises(XyzParseError, match="no atoms"):
        parse_xyz("  \n")


def test_formula_uses_hill_order():
    atoms = parse_xyz("Ru 0 0 0\nCl 0 0 1\nCl 0 0 2\nH 0 1 0\nC 1 0 0\nN 2 0 0\n")
    assert formula(atoms) == "CHCl2NRu"
    assert formula(parse_xyz("Ru 0 0 0\nCl 0 0 1\nCl 1 0 0\n")) == "Cl2Ru"


def test_format_round_trips():
    atoms = parse_xyz(WATER)
    text = format_xyz(atoms, comment="water")
    assert text.splitlines()[:2] == ["3", "water"]
    assert parse_xyz(text) == atoms
