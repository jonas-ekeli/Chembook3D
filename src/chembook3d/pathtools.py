#!/usr/bin/env python3
"""Helpers for a scan path job in a Claude Code cloud session (D114). Standard library only.

Written by Chembook3D into `.claude/chembook3d/pathtools.py` of the investigation's repository
(D93), with the same bond rule and fitted RMSD the app uses (`services/atom_matching.py`,
`services/geometry.py`), so the cloud session and the app agree on what a path does.

    python3 pathtools.py diff START.xyz END.xyz
        bonds that form and break, fitted RMSD, the distances that change most, and the bonds
        that turn by more than 30° (one dihedral each, both ways round, and a ready concerted
        `$scan` block) between two structures of the same atoms in the same order
    python3 pathtools.py rmsd A.xyz B.xyz
    python3 pathtools.py measure FILE.xyz 3 7 [9 [12]]
        a distance (Å), angle or dihedral (degrees, xTB's and IUPAC's sign convention); on a
        multi-structure file, one value per structure
    python3 pathtools.py frames xtbscan.log [--last | --index N] -o OUT.xyz
        structures of a multi-structure file (an xtbscan.log, a path.xyz) as an xyz file:
        all of them, the last, or number N
    python3 pathtools.py trace xtbscan.log --input scan.inp
    python3 pathtools.py trace xtbscan.log --atoms 2 3 [--to 3.2] [--atoms 1 2 3 4 ...]
        each structure's energy (kcal/mol from the first) and the coordinates' values; with the
        stage's scan.inp (or --to, one per --atoms), whether each scanned coordinate started at
        its start value, moved and reached its target and each held one stayed, with exit
        status 1 when a scan barely moved or its first structure slipped away from the start
        value: xTB ends normally in both cases
    python3 pathtools.py pathfinder xtbpath.xyz path.out -o STAGE.xyz
        xTB's path finder (`xtb A.xyz --path B.xyz`) writes energies in kcal/mol from its first
        structure; this writes them in Eh (from the run's output), so the path joins as a stage
    python3 pathtools.py join OUT.xyz STAGE1.log [STAGE2.log ...] --call "xtb ..." [--reverse N]
        every structure of the stages' xtbscan.log files in order, as one path.xyz whose
        comment lines read `energy: <Eh> stage: <n> call: <xtb command line>`; --reverse 2
        turns stage 2 round (scanned from the end back), --call may be given once per stage; a
        final optimisation's xtbopt.xyz joins as a one-structure stage as it is
    python3 pathtools.py check PATH.xyz START.xyz END.xyz [--mapping mapping.json]
                                [--settings path.json] [--json]
        how close the path starts and ends to the two ends (with the settings, the end is judged
        on the reacting atoms `active` lists, and the RMSD over all atoms is given beside it),
        bonds that form or break along it
        other than those the ends differ by, the largest jump between neighbouring structures,
        and the highest point, with spikes (one structure far above both neighbours with a jump
        into or out of it, as a conformer flipping in one step gives) reported apart
    python3 pathtools.py mode TS.xyz MODE [--atoms 2 3 [--atoms ...]] [--json]
        the imaginary mode of MODE (the app's `inputs/start_mode.json` or `end_mode.json`, or
        the g98.out of `xtb TS.xyz --hess`): its wavenumber, the five distances that change
        most along it, each coordinate's change, and whether the mode runs along the given
        distances (each changes by at least 35 % of the largest change, as the app suggests
        coordinates to hold)
    python3 pathtools.py displace TS.xyz MODE --toward END.xyz [--step 0.1] -o OUT.xyz
        the TS pushed along its imaginary mode (the atom moving most by STEP Å), in the
        direction that brings it closer to END.xyz: where a path downhill from the TS starts

Atoms are numbered from 1 everywhere, on the command line and in the functions. Run this file
as a script (`python3 pathtools.py ...`); importing it is not needed.
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
SPIKE_KCAL = 10.0  # kcal/mol above both neighbours, with a jump over GOOD_JUMP: a spike
MOVED = 0.1  # a driven coordinate moved when it covered at least this part of the way
REACHED = {2: 0.1, 3: 5.0, 4: 5.0}  # Å or degrees from its target: reached
ACTIVE_REACH = 2  # bonds out from a reacting or held atom: the atoms an end is judged on (D118)
ROTATION = 30.0  # degrees: a bond whose dihedral changes more is a rotation to drive (D118)
FIRST_POINT = {2: 0.05, 3: 5.0, 4: 5.0}  # a scan's first structure this far off has slipped
PARTIAL_SCALE = 1.8  # × the sum of covalent radii: pairs a mode can make or break (D114)
MODE_SHARE = 0.35  # a distance runs along a mode when it changes this much of the largest


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
    """The a-b-c-d dihedral in degrees, -180 to 180: 0 when a and d are on the same side (cis),
    positive when a turns clockwise onto d looking from b to c (IUPAC; xTB and the app's 3D
    view use the same)."""
    b1 = _sub(b[1:4], a[1:4])
    b2 = _sub(c[1:4], b[1:4])
    b3 = _sub(d[1:4], c[1:4])
    n1, n2 = _cross(b1, b2), _cross(b2, b3)
    # atan2(|b2| b1·(b2×b3), (b1×b2)·(b2×b3)), as the 3D view's chem.ts measures it.
    return math.degrees(math.atan2(math.sqrt(_dot(b2, b2)) * _dot(b1, n2), _dot(n1, n2)))


def _measure(rows, atoms):
    picked = [rows[i] for i in atoms]
    return [None, None, distance, angle, dihedral][len(atoms)](*picked)


def measure(rows, atoms):
    """Distance (Å), angle or dihedral (degrees) of 2 to 4 atoms numbered from 1."""
    if not 2 <= len(atoms) <= 4 or min(atoms) < 1 or max(atoms) > len(rows):
        raise ValueError(f"give 2 to 4 atom numbers from 1 to {len(rows)}")
    return _measure(rows, [a - 1 for a in atoms])


def bonds(rows):
    """Bonded pairs (i, j), i < j, numbered from 1, by the app's rule."""
    return {(i + 1, j + 1) for i, j in _bonds(rows)}


def _bonds(rows):
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
    """0-based pairs formed and broken from start to end."""
    a, b = _bonds(start), _bonds(end)
    return sorted(b - a), sorted(a - b)


def diff(start, end, top=10):
    if [r[0] for r in start] != [r[0] for r in end]:
        raise ValueError("the structures must hold the same atoms in the same order")
    formed, broken = bond_changes(start, end)
    either = _bonds(start) | _bonds(end)
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
    rotations = _rotations(start, end, _bonds(start) & _bonds(end), near)
    return {
        "rmsd": rmsd(start, end),
        "formed": [[i + 1, j + 1] for i, j in formed],
        "broken": [[i + 1, j + 1] for i, j in broken],
        "distances": [
            {"atoms": atoms, "start": round(d0, 3), "end": round(d1, 3)}
            for _, atoms, d0, d1 in distances[:top]
        ],
        "rotations": rotations,
        "scan_block": _scan_block(rotations),
    }


def _rotations(start, end, kept, near):
    """Bonds kept at both ends that turn by more than ROTATION degrees, one dihedral each
    (heavy atoms at its ends where there are), largest first. A bond in a ring, or with only
    hydrogens beyond one end (a methyl group), is left out."""
    found = {}
    for j, k in kept:
        ends_j, ends_k = near[j] - {k}, near[k] - {j}
        if not ends_j or not ends_k or _in_ring(near, j, k):
            continue
        if all(start[x][0] == "H" for x in ends_j) or all(start[x][0] == "H" for x in ends_k):
            continue
        best = None
        for i in ends_j:
            for m in ends_k - {i}:
                t0, t1 = _measure(start, [i, j, k, m]), _measure(end, [i, j, k, m])
                turn = _wrapped(t1 - t0, 4)
                hydrogens = (start[i][0] == "H") + (start[m][0] == "H")
                item = (hydrogens, -abs(turn), [i + 1, j + 1, k + 1, m + 1], t0, t1, turn)
                if best is None or item[:2] < best[:2]:
                    best = item
        if best is not None and abs(best[5]) > ROTATION:
            found[(j, k)] = best
    out = []
    for _, _, atoms, t0, t1, turn in sorted(found.values(), key=lambda b: b[1]):
        other = turn - 360 if turn > 0 else turn + 360
        out.append({
            "bond": f"{atoms[1]}-{atoms[2]}",
            "atoms": atoms,
            "start": round(t0, 1),
            "end": round(t1, 1),
            "scan": [round(t0, 1), round(t0 + turn, 1)],
            "other_way": [round(t0, 1), round(t0 + other, 1)],
        })  # fmt: skip
    return out


def _in_ring(near, j, k):
    """True when atoms j and k stay connected without their own bond: it cannot turn freely."""
    seen, todo = {j}, [j]
    while todo:
        a = todo.pop()
        for b in near[a]:
            if (a, b) in ((j, k), (k, j)) or b in seen:
                continue
            if b == k:
                return True
            seen.add(b)
            todo.append(b)
    return False


def _scan_block(rotations, points=20):
    """An xTB input driving every rotation together, the short way round (xTB goes from the
    first value to the second as written, past ±180° too)."""
    if not rotations:
        return ""
    lines = ["$constrain", "  force constant=1.0"]
    lines += [
        "  dihedral: " + ", ".join(map(str, r["atoms"])) + f", {r['scan'][0]}" for r in rotations
    ]
    lines += ["$scan", "  mode=concerted"]
    lines += [
        f"  {n}: {r['scan'][0]}, {r['scan'][1]}, {points}" for n, r in enumerate(rotations, start=1)
    ]
    return "\n".join([*lines, "$end"])


def join(stages, calls, reversed_stages=()):
    """path.xyz text from the stages' xtbscan.log files."""
    out = []
    for n, path in enumerate(stages, start=1):
        if str(path).replace("\\", "/").rsplit("/", 1)[-1].startswith("xtbpath"):
            raise ValueError(
                f"{path}: xTB's path finder writes energies in kcal/mol from its first "
                "structure; convert it with `pathtools.py pathfinder` and join that"
            )
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


def path_finder(path, output):
    """xyz text of xTB's path finder result with its energies in Eh: the file gives kcal/mol
    from its first structure, the run's output the first structure's total energy."""
    first = None
    with open(output, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "TOTAL ENERGY" in line:
                first = float(line.split("TOTAL ENERGY")[1].split()[0])
                break
    if first is None:
        raise ValueError(f"{output}: no TOTAL ENERGY (give the output of the --path run)")
    out = []
    for comment, rows in read_frames(path):
        kcal = energy_of(comment)
        if kcal is None:
            raise ValueError(f"{path}: a structure has no `energy:` on its comment line")
        out.append(format_frame(rows, f" energy: {first + kcal / HARTREE_KCAL:.12f}"))
    return "".join(out)


def active_atoms(start, end, pairs=(), held=()):
    """The atoms (from 1) an end is judged on (D118): those of the bonds that form or break
    between `start` and `end`, of `pairs` and of the `held` coordinates (lists of atoms from 1),
    and every atom up to ACTIVE_REACH bonds from them at either end. Empty when nothing
    reacts or is held: then the whole structure counts."""
    formed, broken = bond_changes(start, end)
    found = {i for pair in formed + broken for i in pair}
    found |= {a - 1 for group in (*pairs, *held) for a in group}
    if not found:
        return []
    near = {i: set() for i in range(len(start))}
    for i, j in _bonds(start) | _bonds(end):
        near[i].add(j)
        near[j].add(i)
    for _ in range(ACTIVE_REACH):
        found |= {j for i in found for j in near[i]}
    return sorted(i + 1 for i in found)


def frames_of(path, index=None):
    """The structures of a multi-structure file, or only number `index` (from 1; -1 the last)."""
    frames = read_frames(path)
    if index is not None:
        if index == -1:
            index = len(frames)
        if not 1 <= index <= len(frames):
            raise ValueError(f"{path} holds {len(frames)} structures")
        frames = [frames[index - 1]]
    return frames


def _wrapped(change, size):
    return (change + 180) % 360 - 180 if size == 4 else change


def read_scan_input(path):
    """The `$constrain` coordinates ({"atoms", "value"}, value None for `auto`) and `$scan`
    lines ({"constraint" (from 1), "start", "end", "points"}) of an xTB input file."""
    sizes = {"distance": 2, "angle": 3, "dihedral": 4}
    block, constraints, scans = None, [], []
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.split("#")[0].strip()
            if not line:
                continue
            if line.startswith("$"):
                block = None if line.startswith("$end") else line[1:].split()[0].lower()
                continue
            name, colon, rest = line.partition(":")
            name = name.strip().lower()
            if block == "constrain" and colon and name in sizes:
                parts = [x.strip() for x in rest.split(",")]
                size = sizes[name]
                value = None if parts[size].lower() == "auto" else float(parts[size])
                constraints.append({"atoms": [int(x) for x in parts[:size]], "value": value})
            elif block == "scan" and colon and name.isdigit():
                first, last, points = (x.strip() for x in rest.split(",")[:3])
                scans.append({"constraint": int(name), "start": float(first),
                              "end": float(last), "points": int(points)})  # fmt: skip
    return constraints, scans


def trace_input(path, input_path):
    """`trace` with the coordinates, start values and targets of the stage's scan input: each
    scanned constraint from its start to its end value, each other one held at its value."""
    constraints, scans = read_scan_input(input_path)
    coordinates, starts, targets = [], [], []
    scanned = {}
    for scan in scans:
        if not 1 <= scan["constraint"] <= len(constraints):
            raise ValueError(
                f"{input_path}: $scan line {scan['constraint']} names no $constrain line "
                f"(there are {len(constraints)}; they count from 1 in the order listed)"
            )
        scanned[scan["constraint"]] = scan
    for n, item in enumerate(constraints, start=1):
        scan = scanned.get(n)
        if scan is None and item["value"] is None:
            continue
        coordinates.append(item["atoms"])
        starts.append(scan["start"] if scan else item["value"])
        targets.append(scan["end"] if scan else item["value"])
    if not coordinates:
        raise ValueError(f"{input_path}: no coordinate with a value to follow")
    return trace(path, coordinates, targets, starts)


def trace(path, coordinates, targets=None, starts=None):
    """Each structure's energy and the coordinates' values; with targets (one per coordinate),
    whether each moved and reached its target; with start values, whether the first structure
    is at them (a first point that slipped away before the scan began is reported)."""
    frames = read_frames(path)
    if not frames:
        raise ValueError(f"{path} holds no structure")
    energies = [energy_of(comment) for comment, _ in frames]
    rows = []
    for index, (_, structure) in enumerate(frames, start=1):
        row = {"structure": index}
        if energies[0] is not None and energies[index - 1] is not None:
            row["kcal"] = round((energies[index - 1] - energies[0]) * HARTREE_KCAL, 2)
        for atoms in coordinates:
            row["-".join(map(str, atoms))] = round(measure(structure, atoms), 3)
        rows.append(row)
    report = {"structures": rows}
    if targets:
        if len(targets) != len(coordinates):
            raise ValueError("give one target per coordinate")
        verdicts = []
        for atoms, target in zip(coordinates, targets, strict=True):
            first = measure(frames[0][1], atoms)
            last = measure(frames[-1][1], atoms)
            asked = abs(_wrapped(target - first, len(atoms)))
            went = abs(_wrapped(last - first, len(atoms)))
            verdict = {
                "atoms": "-".join(map(str, atoms)),
                "first": round(first, 3),
                "last": round(last, 3),
                "target": target,
                "moved": asked == 0 or went >= MOVED * asked,
                "reached": abs(_wrapped(target - last, len(atoms))) <= REACHED[len(atoms)],
            }
            if starts:
                start = starts[len(verdicts)]
                verdict["start"] = start
                verdict["first_slipped"] = (
                    abs(_wrapped(first - start, len(atoms))) > FIRST_POINT[len(atoms)]
                )
                asked = abs(_wrapped(target - start, len(atoms)))
                verdict["moved"] = asked == 0 or went >= MOVED * asked
            verdicts.append(verdict)
        report["targets"] = verdicts
        report["warnings"] = (
            [
                f"{v['atoms']}: the first structure is at {v['first']}, not at the start value "
                f"{v['start']}: it relaxed away before the scan began (check the $constrain value "
                "and the structure the stage starts from)"
                for v in verdicts
                if v.get("first_slipped")
            ]
            + [
                f"{v['atoms']}: went only from {v['first']} to {v['last']} on the way to "
                f"{v['target']}: the scan did not drive it (check the $scan line's number)"
                for v in verdicts
                if not v["moved"]
            ]
            + [
                f"{v['atoms']}: held at {v['target']} but ended at {v['last']}: it was driven or "
                "drifted (check the $scan line's number and the force constant)"
                for v in verdicts
                if v.get("start") == v["target"] and not v["reached"]
            ]
        )
    return report


def _spikes(energies, jumps):
    """Structures (0-based) far above both neighbours with a jump into or out of them."""
    found = []
    for k in range(1, len(energies) - 1):
        above = (energies[k] - max(energies[k - 1], energies[k + 1])) * HARTREE_KCAL
        if above > SPIKE_KCAL and max(jumps[k - 1], jumps[k]) > GOOD_JUMP:
            found.append(k)
    return found


def _top(energies, skip=()):
    inner = [k for k in range(1, len(energies) - 1) if k not in skip
             and energies[k] > energies[k - 1] and energies[k] > energies[k + 1]]  # fmt: skip
    return max(inner, key=lambda k: energies[k]) if inner else None


def check(path, start, end, allowed=None, active=None):
    """How a path does: its ends, stray bond changes, jumps, spikes and its highest point.
    `allowed` lists more bonds (atom pairs from 1) that may form or break; with `active` (atoms
    from 1, `active_atoms`), the end is reached when those atoms fit the end (D118), and the
    RMSD over all atoms is given beside it."""
    frames = read_frames(path)
    if not frames:
        raise ValueError(f"{path} holds no structure")
    allowed = {tuple(sorted((a - 1, b - 1))) for a, b in (allowed or [])}
    allowed |= {tuple(pair) for pair in bond_changes(start, end)[0] + bond_changes(start, end)[1]}
    first_bonds = _bonds(start)
    stray = []
    for index, (_, rows) in enumerate(frames, start=1):
        for pair in sorted(_bonds(rows) ^ first_bonds):
            if pair not in allowed:
                stray.append({"structure": index, "atoms": [pair[0] + 1, pair[1] + 1]})
    jumps = [rmsd(frames[k][1], frames[k + 1][1]) for k in range(len(frames) - 1)]
    energies = [energy_of(comment) for comment, _ in frames]
    top = smooth_top = None
    spikes = []
    if all(e is not None for e in energies) and len(energies) > 2:
        spikes = _spikes(energies, jumps)
        top = _top(energies)
        smooth_top = _top(energies, set(spikes))

    def barrier(k):
        return (energies[k] - energies[0]) * HARTREE_KCAL if k is not None else None

    end_rmsd = rmsd(frames[-1][1], end)
    picked = [a - 1 for a in active or [] if 1 <= a <= len(end)]
    judged = end_rmsd
    if len(picked) >= 3 and len(picked) < len(end):
        judged = rmsd([frames[-1][1][k] for k in picked], [end[k] for k in picked])
    else:
        picked = []
    largest = max(jumps) if jumps else 0.0
    report = {
        "structures": len(frames),
        "start_rmsd": rmsd(frames[0][1], start),
        "end_rmsd": end_rmsd,
        "active_atoms": len(picked) or len(end),
        "active_end_rmsd": judged,
        "judged_on": "the reacting atoms" if picked else "all atoms",
        "reached_end": judged <= GOOD_END_RMSD,
        "stray_bond_changes": stray[:50],
        "largest_jump": largest,
        "largest_jump_after": jumps.index(largest) + 1 if jumps else None,
        "top": top + 1 if top is not None else None,
        "barrier_kcal": barrier(top),
        "spikes": [k + 1 for k in spikes],
        "top_without_spikes": smooth_top + 1 if smooth_top is not None else None,
        "barrier_without_spikes_kcal": barrier(smooth_top),
    }
    report["good"] = report["reached_end"] and not stray and largest <= GOOD_JUMP
    return report


# ---------- a TS's imaginary mode ----------


def _g98_mode(lines):
    """The most negative mode of a Gaussian-style frequency output (xTB's g98.out)."""
    best = None
    k = 0
    while k < len(lines):
        line = lines[k]
        if line.strip().startswith("Frequencies --"):
            wavenumbers = [float(x) for x in line.split("--", 1)[1].split()]
            while k < len(lines) and not lines[k].split()[:2] == ["Atom", "AN"]:
                k += 1
            columns = [[] for _ in wavenumbers]
            k += 1
            while k < len(lines):
                parts = lines[k].split()
                if len(parts) != 2 + 3 * len(wavenumbers) or not parts[0].isdigit():
                    break
                for m in range(len(wavenumbers)):
                    columns[m].append([float(x) for x in parts[2 + 3 * m : 5 + 3 * m]])
                k += 1
            for wavenumber, vectors in zip(wavenumbers, columns, strict=True):
                if best is None or wavenumber < best[0]:
                    best = (wavenumber, vectors)
            continue
        k += 1
    return best


def read_mode(path):
    """(wavenumber in cm⁻¹, negative for imaginary; one [x, y, z] per atom) from the app's mode
    file (JSON with `wavenumber` and `vectors`) or a g98.out."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if text.lstrip().startswith("{"):
        data = json.loads(text)
        return float(data["wavenumber"]), [[float(x) for x in v] for v in data["vectors"]]
    found = _g98_mode(text.splitlines())
    if found is None:
        raise ValueError(f"{path}: no frequencies found")
    return found


def _rate(rows, vectors, atoms):
    """How fast a coordinate changes along the mode (per Å of the atom moving most)."""
    largest = max(math.sqrt(_dot(v, v)) for v in vectors) or 1.0
    h = 1e-4 / largest

    def moved(sign):
        return [[r[0], *(x + sign * h * dx for x, dx in zip(r[1:4], v, strict=True))]
                for r, v in zip(rows, vectors, strict=True)]  # fmt: skip

    change = measure(moved(1), atoms) - measure(moved(-1), atoms)
    return _wrapped(change, len(atoms)) / (2 * h * largest)


def mode_report(rows, mode, coordinates=()):
    """The mode's wavenumber, the five distances that change most along it, each coordinate's
    change, and whether it runs along the given distances (D114's 35 % rule)."""
    wavenumber, vectors = mode
    if len(vectors) != len(rows):
        raise ValueError(f"the mode has {len(vectors)} atoms and the structure {len(rows)}")
    n = len(rows)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            reach = PARTIAL_SCALE * (
                RADII.get(rows[i][0], DEFAULT_RADIUS) + RADII.get(rows[j][0], DEFAULT_RADIUS)
            )
            if distance(rows[i], rows[j]) < reach:
                pairs.append((abs(_rate(rows, vectors, [i + 1, j + 1])), i, j))
    pairs.sort(reverse=True)
    largest = pairs[0][0] if pairs and pairs[0][0] > 0 else None
    report = {
        "wavenumber": wavenumber,
        "imaginary": wavenumber < 0,
        "top_distances": [
            {
                "atoms": f"{i + 1}-{j + 1}",
                "value": round(distance(rows[i], rows[j]), 3),
                "share": round(rate / largest, 2) if largest else 0.0,
            }
            for rate, i, j in pairs[:5]
        ],  # fmt: skip
    }
    held = []
    for atoms in coordinates:
        rate = _rate(rows, vectors, atoms)
        row = {"atoms": "-".join(map(str, atoms)), "value": round(measure(rows, atoms), 3)}
        row["change"] = round(rate, 3)
        if len(atoms) == 2:
            row["share"] = round(abs(rate) / largest, 2) if largest else 0.0
        held.append(row)
    report["coordinates"] = held
    shares = [row["share"] for row in held if "share" in row]
    report["runs_along"] = (
        wavenumber < 0 and all(x >= MODE_SHARE for x in shares) if shares else None
    )
    return report


def displace(rows, mode, toward, step=0.1):
    """The structure pushed along the mode so the atom moving most moves `step` Å, in the
    direction whose result fits `toward` better."""
    _, vectors = mode
    largest = max(math.sqrt(_dot(v, v)) for v in vectors)
    if largest == 0:
        raise ValueError("the mode moves no atom")
    pushed = []
    for sign in (1, -1):
        scale = sign * step / largest
        moved = [[r[0], *(x + scale * dx for x, dx in zip(r[1:4], v, strict=True))]
                 for r, v in zip(rows, vectors, strict=True)]  # fmt: skip
        pushed.append((rmsd(moved, toward), sign, moved))
    pushed.sort(key=lambda item: item[0])
    return pushed[0][2]


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
    p = sub.add_parser("frames")
    p.add_argument("file")
    which = p.add_mutually_exclusive_group()
    which.add_argument("--last", action="store_true")
    which.add_argument("--index", type=int)
    p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("trace")
    p.add_argument("file")
    p.add_argument("--input")
    p.add_argument("--atoms", action="append", nargs="+", type=int)
    p.add_argument("--to", action="append", type=float)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("mode")
    p.add_argument("ts")
    p.add_argument("mode")
    p.add_argument("--atoms", action="append", nargs="+", type=int, default=[])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("displace")
    p.add_argument("ts")
    p.add_argument("mode")
    p.add_argument("--toward", required=True)
    p.add_argument("--step", type=float, default=0.1)
    p.add_argument("-o", "--out", required=True)
    p = sub.add_parser("pathfinder")
    p.add_argument("path")
    p.add_argument("output")
    p.add_argument("-o", "--out", required=True)
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
    p.add_argument("--settings")
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
            print(f"{measure(rows, args.atoms):.4f}")
    elif args.command == "frames":
        picked = frames_of(args.file, -1 if args.last else args.index)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write("".join(format_frame(rows, comment) for comment, rows in picked))
        print(f"{args.out}: {len(picked)} structures")
    elif args.command == "trace":
        if args.input:
            report = trace_input(args.file, args.input)
        elif args.atoms:
            report = trace(args.file, args.atoms, args.to)
        else:
            parser.error("give the stage's --input scan.inp or --atoms")
        _print(report, args.json)
        if report.get("warnings"):
            return 1
    elif args.command == "pathfinder":
        text = path_finder(args.path, args.output)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"{args.out}: {text.count(' energy: ')} structures, energies in Eh")
    elif args.command == "mode":
        _print(mode_report(read_xyz(args.ts), read_mode(args.mode), args.atoms), args.json)
    elif args.command == "displace":
        rows = displace(read_xyz(args.ts), read_mode(args.mode), read_xyz(args.toward), args.step)
        comment = f" pushed {args.step} A along the imaginary mode toward {args.toward}"
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(format_frame(rows, comment))
        print(f"{args.out}: rmsd to {args.toward} {rmsd(rows, read_xyz(args.toward)):.4f}")
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
            allowed = mapping.get("formed", []) + mapping.get("broken", [])
        active = None
        if args.settings:
            with open(args.settings, encoding="utf-8") as handle:
                active = json.load(handle).get("active")
        report = check(args.path, read_xyz(args.start), read_xyz(args.end), allowed, active)
        _print(report, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
