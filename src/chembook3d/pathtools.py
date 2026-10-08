#!/usr/bin/env python3
"""Helpers for a scan path job in a Claude Code cloud session (D114). Standard library only.

Written by Chembook3D into `.claude/chembook3d/pathtools.py` of the investigation's repository
(D93), with the same bond rule and fitted RMSD the app uses (`services/atom_matching.py`,
`services/geometry.py`), so the cloud session and the app agree on what a path does.

    python3 pathtools.py diff START.xyz END.xyz
        bonds that form and break, fitted RMSD, and the distances and dihedrals that change
        most between two structures of the same atoms in the same order
    python3 pathtools.py rmsd A.xyz B.xyz
    python3 pathtools.py measure FILE.xyz 3 7 [9 [12]]
        a distance (Å), angle or dihedral (degrees); atoms 1-based; on a multi-structure file,
        one value per structure
    python3 pathtools.py join OUT.xyz STAGE1.log [STAGE2.log ...] --call "xtb ..." [--reverse N]
        every structure of the stages' xtbscan.log files in order, as one path.xyz whose
        comment lines read `energy: <Eh> stage: <n> call: <xtb command line>`; --reverse 2
        turns stage 2 round (scanned from the end back), --call may be given once per stage
    python3 pathtools.py check PATH.xyz START.xyz END.xyz [--mapping mapping.json] [--json]
        how close the path starts and ends to the two ends, bonds that form or break along it
        other than those the ends differ by, the largest jump between neighbouring structures,
        and the highest point
"""

import argparse
import json
import math
import sys

# Covalent radii in Å (Cordero et al. 2008), as the app's 3D view uses them.
RADII = {
    "H": 0.31, "B": 0.84, "C": 0.76, "N": 0.71, "O": 0.66, "F": 0.57, "Si": 1.11, "P": 1.07,
    "S": 1.05, "Cl": 1.02, "Br": 1.2, "I": 1.39, "Li": 1.28, "Na": 1.66, "K": 2.03, "Mg": 1.41,
    "Al": 1.21, "Fe": 1.32, "Co": 1.26, "Ni": 1.24, "Cu": 1.32, "Zn": 1.22, "Ru": 1.46,
    "Rh": 1.42, "Pd": 1.39, "Ag": 1.45, "Mo": 1.54, "W": 1.62, "Os": 1.44, "Ir": 1.41,
    "Pt": 1.36, "Au": 1.36, "Ti": 1.6, "Zr": 1.75, "Cr": 1.39, "Mn": 1.39, "Sn": 1.39,
}  # fmt: skip
DEFAULT_RADIUS = 1.5
BOND_SCALE = 1.2
MIN_DISTANCE = 0.4
HARTREE_KCAL = 627.5094740631

# What a good path keeps to (D114).
GOOD_END_RMSD = 0.5  # Å, the path's last structure against the end
GOOD_JUMP = 0.5  # Å, fitted RMSD between neighbouring structures


# ---------- reading and writing ----------


def element(token):
    token = token.strip()
    if token.isdigit():
        numbers = {1: "H", 5: "B", 6: "C", 7: "N", 8: "O", 9: "F", 14: "Si", 15: "P", 16: "S"}
        numbers.update({17: "Cl", 35: "Br", 53: "I", 26: "Fe", 28: "Ni", 44: "Ru", 46: "Pd"})
        return numbers.get(int(token), token)
    return token[:1].upper() + token[1:].lower()


def read_frames(path):
    """Every structure of an xyz file: (comment, [[element, x, y, z], ...])."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    frames, i = [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        count = int(lines[i].split()[0])
        comment = lines[i + 1] if i + 1 < len(lines) else ""
        rows = []
        for line in lines[i + 2 : i + 2 + count]:
            parts = line.split()
            rows.append([element(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])])
        if len(rows) < count:
            break  # cut off
        frames.append((comment, rows))
        i += 2 + count
    return frames


def read_xyz(path):
    return read_frames(path)[0][1]


def energy_of(comment):
    """The energy on an xTB comment line (` energy: -15.4 gnorm: ...`), or None."""
    parts = comment.split()
    for key, value in zip(parts, parts[1:], strict=False):
        if key == "energy:":
            try:
                return float(value)
            except ValueError:
                return None
    return None


def format_frame(rows, comment):
    lines = [str(len(rows)), comment]
    lines += [f"{e:<2} {x:15.8f} {y:15.8f} {z:15.8f}" for e, x, y, z in rows]
    return "\n".join(lines) + "\n"


# ---------- geometry ----------


def distance(a, b):
    return math.dist(a[1:4], b[1:4])


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def angle(a, b, c):
    u, v = _sub(a[1:4], b[1:4]), _sub(c[1:4], b[1:4])
    cosine = _dot(u, v) / math.sqrt(_dot(u, u) * _dot(v, v))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def dihedral(a, b, c, d):
    b0 = _sub(a[1:4], b[1:4])
    b1 = _sub(c[1:4], b[1:4])
    b2 = _sub(d[1:4], c[1:4])
    n1, n2 = _cross(b0, b1), _cross(b1, b2)
    m1 = _cross(n1, [x / math.sqrt(_dot(b1, b1)) for x in b1])
    return math.degrees(math.atan2(_dot(m1, n2), _dot(n1, n2)))


def measure(rows, atoms):
    """Distance, angle or dihedral of 0-based atoms."""
    picked = [rows[i] for i in atoms]
    return [None, None, distance, angle, dihedral][len(atoms)](*picked)


def bonds(rows):
    """Bonded pairs (i, j), i < j, 0-based, by the app's rule."""
    found = set()
    for i in range(len(rows)):
        ri = RADII.get(rows[i][0], DEFAULT_RADIUS)
        for j in range(i + 1, len(rows)):
            limit = BOND_SCALE * (ri + RADII.get(rows[j][0], DEFAULT_RADIUS))
            d = distance(rows[i], rows[j])
            if MIN_DISTANCE < d < limit:
                found.add((i, j))
    return found


def _jacobi_largest(m):
    """Largest eigenvalue of a symmetric 4×4 matrix (Jacobi rotations)."""
    a = [row[:] for row in m]
    for _ in range(100):
        off = sum(abs(a[p][q]) for p in range(4) for q in range(p + 1, 4))
        if off < 1e-14:
            break
        for p in range(4):
            for q in range(p + 1, 4):
                if abs(a[p][q]) < 1e-18:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(4):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(4):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
    return max(a[i][i] for i in range(4))


def rmsd(a, b):
    """RMSD in Å after the best rotation and translation (never a mirror image), atom by atom
    in the given order (Horn's quaternion method)."""
    if len(a) != len(b) or not a:
        raise ValueError("the structures have different numbers of atoms")
    n = len(a)
    ca = [sum(r[k] for r in a) / n for k in (1, 2, 3)]
    cb = [sum(r[k] for r in b) / n for k in (1, 2, 3)]
    p = [_sub(r[1:4], ca) for r in a]
    q = [_sub(r[1:4], cb) for r in b]
    s = [[sum(x[i] * y[j] for x, y in zip(p, q, strict=True)) for j in range(3)] for i in range(3)]
    (sxx, sxy, sxz), (syx, syy, syz), (szx, szy, szz) = s
    m = [
        [sxx + syy + szz, syz - szy, szx - sxz, sxy - syx],
        [syz - szy, sxx - syy - szz, sxy + syx, szx + sxz],
        [szx - sxz, sxy + syx, -sxx + syy - szz, syz + szy],
        [sxy - syx, szx + sxz, syz + szy, -sxx - syy + szz],
    ]
    largest = _jacobi_largest(m)
    total = sum(_dot(x, x) for x in p) + sum(_dot(y, y) for y in q)
    return math.sqrt(max(0.0, (total - 2 * largest) / n))


# ---------- commands ----------


def bond_changes(start, end):
    a, b = bonds(start), bonds(end)
    return sorted(b - a), sorted(a - b)


def diff(start, end, top=10):
    if [r[0] for r in start] != [r[0] for r in end]:
        raise ValueError("the structures must hold the same atoms in the same order")
    formed, broken = bond_changes(start, end)
    either = bonds(start) | bonds(end)
    distances = []
    for i in range(len(start)):
        for j in range(i + 1, len(start)):
            d0, d1 = distance(start[i], start[j]), distance(end[i], end[j])
            if (i, j) in either or min(d0, d1) < 3.5:
                distances.append((abs(d1 - d0), [i + 1, j + 1], d0, d1))
    distances.sort(key=lambda item: -item[0])
    near = {i: set() for i in range(len(start))}
    for i, j in either:
        near[i].add(j)
        near[j].add(i)
    torsions = {}
    for j, k in either:
        for i in near[j] - {k}:
            for m in near[k] - {j, i}:
                key = (i, j, k, m) if i < m else (m, k, j, i)
                if key in torsions:
                    continue
                t0, t1 = measure(start, list(key)), measure(end, list(key))
                change = abs((t1 - t0 + 180) % 360 - 180)
                torsions[key] = (change, [x + 1 for x in key], t0, t1)
    dihedrals = sorted(torsions.values(), key=lambda item: -item[0])
    return {
        "rmsd": rmsd(start, end),
        "formed": [[i + 1, j + 1] for i, j in formed],
        "broken": [[i + 1, j + 1] for i, j in broken],
        "distances": [
            {"atoms": atoms, "start": round(d0, 3), "end": round(d1, 3)}
            for _, atoms, d0, d1 in distances[:top]
        ],
        "dihedrals": [
            {"atoms": atoms, "start": round(t0, 1), "end": round(t1, 1)}
            for _, atoms, t0, t1 in dihedrals[:top]
        ],
    }


def join(stages, calls, reversed_stages=()):
    """path.xyz text from the stages' xtbscan.log files."""
    out = []
    for n, path in enumerate(stages, start=1):
        frames = read_frames(path)
        if n in reversed_stages:
            frames = frames[::-1]
        call = calls[n - 1] if len(calls) >= n else calls[-1]
        for comment, rows in frames:
            energy = energy_of(comment)
            if energy is None:
                raise ValueError(f"{path}: a structure has no `energy:` on its comment line")
            out.append(format_frame(rows, f" energy: {energy:.12f} stage: {n} call: {call}"))
    return "".join(out)


def check(path, start, end, allowed=None):
    """How a path does: its ends, stray bond changes, jumps and its highest point."""
    frames = read_frames(path)
    if not frames:
        raise ValueError(f"{path} holds no structure")
    allowed = {tuple(sorted(pair)) for pair in (allowed or [])}
    allowed |= {tuple(pair) for pair in bond_changes(start, end)[0] + bond_changes(start, end)[1]}
    first_bonds = bonds(start)
    stray = []
    for index, (_, rows) in enumerate(frames, start=1):
        for pair in sorted(bonds(rows) ^ first_bonds):
            if pair not in allowed:
                stray.append({"structure": index, "atoms": [pair[0] + 1, pair[1] + 1]})
    jumps = [rmsd(frames[k][1], frames[k + 1][1]) for k in range(len(frames) - 1)]
    energies = [energy_of(comment) for comment, _ in frames]
    top = None
    if all(e is not None for e in energies) and len(energies) > 2:
        inner = [k for k in range(1, len(energies) - 1)
                 if energies[k] > energies[k - 1] and energies[k] > energies[k + 1]]  # fmt: skip
        if inner:
            top = max(inner, key=lambda k: energies[k])
    end_rmsd = rmsd(frames[-1][1], end)
    largest = max(jumps) if jumps else 0.0
    report = {
        "structures": len(frames),
        "start_rmsd": rmsd(frames[0][1], start),
        "end_rmsd": end_rmsd,
        "reached_end": end_rmsd <= GOOD_END_RMSD,
        "stray_bond_changes": stray[:50],
        "largest_jump": largest,
        "largest_jump_after": jumps.index(largest) + 1 if jumps else None,
        "top": top + 1 if top is not None else None,
        "barrier_kcal": ((energies[top] - energies[0]) * HARTREE_KCAL if top is not None else None),
    }
    report["good"] = report["reached_end"] and not stray and largest <= GOOD_JUMP
    return report


def _print(data, as_json):
    if as_json:
        print(json.dumps(data, indent=2))
        return
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            print(f"{key}:")
            for item in value:
                print("  " + ", ".join(f"{k} {v}" for k, v in item.items()))
        else:
            print(f"{key}: {value}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("diff")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("rmsd")
    p.add_argument("a")
    p.add_argument("b")
    p = sub.add_parser("measure")
    p.add_argument("file")
    p.add_argument("atoms", nargs="+", type=int)
    p = sub.add_parser("join")
    p.add_argument("out")
    p.add_argument("stages", nargs="+")
    p.add_argument("--call", action="append", required=True)
    p.add_argument("--reverse", action="append", type=int, default=[])
    p = sub.add_parser("check")
    p.add_argument("path")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--mapping")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "diff":
        _print(diff(read_xyz(args.start), read_xyz(args.end)), args.json)
    elif args.command == "rmsd":
        print(f"{rmsd(read_xyz(args.a), read_xyz(args.b)):.4f}")
    elif args.command == "measure":
        if not 2 <= len(args.atoms) <= 4:
            parser.error("give 2 to 4 atom numbers")
        for _, rows in read_frames(args.file):
            print(f"{measure(rows, [a - 1 for a in args.atoms]):.4f}")
    elif args.command == "join":
        text = join(args.stages, args.call, set(args.reverse))
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"{args.out}: {text.count(' energy: ')} structures")
    elif args.command == "check":
        allowed = None
        if args.mapping:
            with open(args.mapping, encoding="utf-8") as handle:
                mapping = json.load(handle)
            pairs = mapping.get("formed", []) + mapping.get("broken", [])
            allowed = [[a - 1, b - 1] for a, b in pairs]
        _print(check(args.path, read_xyz(args.start), read_xyz(args.end), allowed), args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
