"""Small CIFs written for the tests of crystal structures (D234): molecules placed in a cell at
fractional coordinates, so no deposited structure is copied into the repository."""

import gemmi
import numpy as np

from chembook3d import xyz

P21C = ["x,y,z", "-x,y+1/2,-z+1/2", "-x,-y,-z", "x,-y+1/2,z+1/2"]
CELL = (10.0, 8.0, 12.0, 90.0, 100.0, 90.0)


def write_cif(
    molecules: list[tuple[str, list[xyz.Atom], dict]],
    cell: tuple[float, ...] = CELL,
    symops: list[str] = P21C,
    space_group: str = "P 1 21/c 1",
    extra: str = "",
) -> str:
    """Each molecule is (label prefix, atoms in Å, options): `shift` moves it in the cell (Å),
    `part` gives its atoms a disorder group."""
    unit = gemmi.UnitCell(*cell)
    rows = []
    for prefix, atoms, options in molecules:
        shift = np.array(options.get("shift", (0.0, 0.0, 0.0)))
        part = options.get("part", ".")
        for number, atom in enumerate(atoms, start=1):
            point = np.array([atom.x, atom.y, atom.z]) + shift
            f = unit.fractionalize(gemmi.Position(*point))
            rows.append(
                f"{prefix}{atom.element}{number} {atom.element} {f.x:.6f} {f.y:.6f} {f.z:.6f} "
                f"1 {part}"
            )
    ops = "\n".join(f"'{op}'" for op in symops)
    a, b, c, alpha, beta, gamma = cell
    return f"""data_test
_chemical_formula_sum 'C2 H6 O'
_journal_paper_doi 10.0000/test.1
_database_code_depnum_ccdc_archive 'CCDC 1234567'
_cell_length_a {a}
_cell_length_b {b}
_cell_length_c {c}
_cell_angle_alpha {alpha}
_cell_angle_beta {beta}
_cell_angle_gamma {gamma}
_symmetry_space_group_name_H-M '{space_group}'
loop_
_symmetry_equiv_pos_as_xyz
{ops}
{extra}
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
_atom_site_occupancy
_atom_site_disorder_group
{chr(10).join(rows)}
"""
