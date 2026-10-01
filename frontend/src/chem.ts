// Small geometry helpers for the interface: reading xyz text for thumbnails and measuring
// picked atoms. They only draw and measure what the backend stored; no rules live here.

export type Atom = { element: string; x: number; y: number; z: number }
type Vec = [number, number, number]

/** Atom lines of xyz text (count and comment lines are skipped). */
export function parseXyz(text: string): Atom[] {
  const atoms: Atom[] = []
  for (const line of text.split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/)
    if (parts.length < 4) continue
    const [x, y, z] = parts.slice(1, 4).map(Number)
    if (!/^[A-Za-z]{1,3}$/.test(parts[0]) || ![x, y, z].every(Number.isFinite)) continue
    atoms.push({ element: parts[0][0].toUpperCase() + parts[0].slice(1).toLowerCase(), x, y, z })
  }
  return atoms
}

// Covalent radii in Å (Cordero et al. 2008) for bond detection; others default to 1.5 Å.
const RADII: Record<string, number> = {
  H: 0.31, B: 0.84, C: 0.76, N: 0.71, O: 0.66, F: 0.57, Si: 1.11, P: 1.07, S: 1.05, Cl: 1.02,
  Br: 1.2, I: 1.39, Li: 1.28, Na: 1.66, K: 2.03, Mg: 1.41, Al: 1.21, Fe: 1.32, Co: 1.26,
  Ni: 1.24, Cu: 1.32, Zn: 1.22, Ru: 1.46, Rh: 1.42, Pd: 1.39, Ag: 1.45, Mo: 1.54, W: 1.62,
  Os: 1.44, Ir: 1.41, Pt: 1.36, Au: 1.36, Ti: 1.6, Zr: 1.75, Cr: 1.39, Mn: 1.39, Sn: 1.39,
}

// Jmol-style element colours.
const COLOURS: Record<string, string> = {
  H: '#d9d9d9', C: '#707070', N: '#3050f8', O: '#ff0d0d', F: '#90e050', Cl: '#1ff01f',
  Br: '#a62929', I: '#940094', S: '#e0c030', P: '#ff8000', B: '#e0a0a0', Si: '#b09070',
  Ru: '#248f8f', Rh: '#0a7d8c', Pd: '#006985', Fe: '#e06633', Ni: '#50d050', Cu: '#c88033',
  Ir: '#175487', Pt: '#d0d0e0', Au: '#ffd123', Mo: '#54b5b5', W: '#2194d6', Os: '#266696',
}

export function elementColour(element: string): string {
  return COLOURS[element] ?? '#ff1493'
}

export function bonds(atoms: Atom[]): [number, number][] {
  const found: [number, number][] = []
  for (let i = 0; i < atoms.length; i++) {
    const ri = RADII[atoms[i].element] ?? 1.5
    for (let j = i + 1; j < atoms.length; j++) {
      const limit = 1.2 * (ri + (RADII[atoms[j].element] ?? 1.5))
      const dx = atoms[i].x - atoms[j].x
      const dy = atoms[i].y - atoms[j].y
      const dz = atoms[i].z - atoms[j].z
      const d2 = dx * dx + dy * dy + dz * dz
      if (d2 > 0.16 && d2 < limit * limit) found.push([i, j])
    }
  }
  return found
}

/** Eigenvectors of a symmetric 3×3 matrix by Jacobi rotations, largest eigenvalue first. */
function principalAxes(m: number[][]): Vec[] {
  const a = m.map((row) => [...row])
  const v = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ]
  for (let sweep = 0; sweep < 30; sweep++) {
    let off = 0
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) off += Math.abs(a[p][q])
    if (off < 1e-12) break
    for (let p = 0; p < 3; p++) {
      for (let q = p + 1; q < 3; q++) {
        if (Math.abs(a[p][q]) < 1e-15) continue
        const theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1))
        const c = 1 / Math.sqrt(t * t + 1)
        const s = t * c
        for (let k = 0; k < 3; k++) {
          const akp = a[k][p]
          const akq = a[k][q]
          a[k][p] = c * akp - s * akq
          a[k][q] = s * akp + c * akq
        }
        for (let k = 0; k < 3; k++) {
          const apk = a[p][k]
          const aqk = a[q][k]
          a[p][k] = c * apk - s * aqk
          a[q][k] = s * apk + c * aqk
        }
        for (let k = 0; k < 3; k++) {
          const vkp = v[k][p]
          const vkq = v[k][q]
          v[k][p] = c * vkp - s * vkq
          v[k][q] = s * vkp + c * vkq
        }
      }
    }
  }
  const order = [0, 1, 2].sort((i, j) => a[j][j] - a[i][i])
  return order.map((i) => [v[0][i], v[1][i], v[2][i]] as Vec)
}

export type Projected = { element: string; x: number; y: number; depth: number }

/** A unit quaternion [x, y, z, w] turning coordinates (about their centre) into the picture:
 * x right, y up, z towards the viewer. It is 3Dmol's view rotation, so a card drawn with the
 * quaternion saved from the 3D view shows the molecule as it was seen there. */
export type Rotation = [number, number, number, number]

function centred(atoms: Atom[]): Vec[] {
  const n = atoms.length
  const centre = [0, 0, 0]
  for (const a of atoms) {
    centre[0] += a.x / n
    centre[1] += a.y / n
    centre[2] += a.z / n
  }
  return atoms.map((a) => [a.x - centre[0], a.y - centre[1], a.z - centre[2]])
}

/** The quaternion of the rotation matrix whose rows are the picture's x, y and z axes. */
function quaternionOf(rows: [Vec, Vec, Vec]): Rotation {
  const [[m00, m01, m02], [m10, m11, m12], [m20, m21, m22]] = rows
  const trace = m00 + m11 + m22
  let q: Rotation
  if (trace > 0) {
    const s = 0.5 / Math.sqrt(trace + 1)
    q = [(m21 - m12) * s, (m02 - m20) * s, (m10 - m01) * s, 0.25 / s]
  } else if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22)
    q = [0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s]
  } else if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22)
    q = [(m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s]
  } else {
    const s = 2 * Math.sqrt(1 + m22 - m00 - m11)
    q = [(m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s]
  }
  const length = Math.hypot(...q)
  return q.map((v) => v / length) as Rotation
}

/** The default orientation: the molecule's two longest directions in the picture plane. The
 * third axis is their cross product, so the view is a rotation (never a mirror image) and
 * depth points at the viewer. */
export function defaultRotation(atoms: Atom[]): Rotation {
  if (atoms.length === 0) return [0, 0, 0, 1]
  const pts = centred(atoms)
  const cov = [0, 1, 2].map((i) => [0, 1, 2].map((j) => pts.reduce((sum, p) => sum + p[i] * p[j], 0)))
  const [u, w] = principalAxes(cov)
  return quaternionOf([u, w, cross(u, w)])
}

function rotate(q: Rotation, p: Vec): Vec {
  // p' = p + 2w (q × p) + 2 q × (q × p), with q the vector part.
  const v: Vec = [q[0], q[1], q[2]]
  const t = cross(v, p).map((c) => 2 * c) as Vec
  const u = cross(v, t)
  return [p[0] + q[3] * t[0] + u[0], p[1] + q[3] * t[1] + u[1], p[2] + q[3] * t[2] + u[2]]
}

/** Atoms turned into the picture by `rotation`, or by the default orientation. */
export function project(atoms: Atom[], rotation: Rotation | null = null): Projected[] {
  if (atoms.length === 0) return []
  const q = rotation ?? defaultRotation(atoms)
  return centred(atoms).map((p, i) => {
    const [x, y, depth] = rotate(q, p)
    return { element: atoms[i].element, x, y, depth }
  })
}

/** How many hydrogens the 3D views and structure cards draw (a setting). */
export type HydrogenMode = 'all' | 'polar' | 'none'

/** Indices of the atoms not drawn under `mode`: every hydrogen for "none"; for "polar", the
 * hydrogens bonded only to carbon, so hydrides, O–H, N–H and agostic C–H···M stay. */
export function hiddenAtoms(atoms: Atom[], mode: HydrogenMode, pairs: [number, number][] = bonds(atoms)): Set<number> {
  const hidden = new Set<number>()
  if (mode === 'all') return hidden
  if (mode === 'none') {
    atoms.forEach((a, i) => a.element === 'H' && hidden.add(i))
    return hidden
  }
  const partners = new Map<number, string[]>()
  for (const [i, j] of pairs) {
    if (atoms[i].element === 'H') partners.set(i, [...(partners.get(i) ?? []), atoms[j].element])
    if (atoms[j].element === 'H') partners.set(j, [...(partners.get(j) ?? []), atoms[i].element])
  }
  partners.forEach((elements, i) => elements.every((e) => e === 'C') && hidden.add(i))
  return hidden
}

function sub(a: Atom, b: Atom): Vec {
  return [a.x - b.x, a.y - b.y, a.z - b.z]
}
function dot(a: Vec, b: Vec): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}
function cross(a: Vec, b: Vec): Vec {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}
const norm = (a: Vec) => Math.sqrt(dot(a, a))
const degrees = (radians: number) => (radians * 180) / Math.PI

/** FR-3D-02: distance (Å) for 2 atoms, angle for 3, dihedral for 4 (degrees). */
export function measure(atoms: Atom[]): { kind: 'distance' | 'angle' | 'dihedral'; value: number } | null {
  if (atoms.length === 2) return { kind: 'distance', value: norm(sub(atoms[0], atoms[1])) }
  if (atoms.length === 3) {
    const a = sub(atoms[0], atoms[1])
    const b = sub(atoms[2], atoms[1])
    const cos = Math.min(1, Math.max(-1, dot(a, b) / (norm(a) * norm(b))))
    return { kind: 'angle', value: degrees(Math.acos(cos)) }
  }
  if (atoms.length === 4) {
    const b1 = sub(atoms[1], atoms[0])
    const b2 = sub(atoms[2], atoms[1])
    const b3 = sub(atoms[3], atoms[2])
    const n1 = cross(b1, b2)
    const n2 = cross(b2, b3)
    // IUPAC sign convention: atan2(|b2| b1·(b2×b3), (b1×b2)·(b2×b3)).
    return { kind: 'dihedral', value: degrees(Math.atan2(norm(b2) * dot(b1, n2), dot(n1, n2))) }
  }
  return null
}

/** D80: atom numbers typed as numbers and ranges ("1-12, 15"), 1-based, in the order given
 * (ranges run upwards). Returns the numbers or the reason they cannot be read; whether the
 * atoms exist and pair up is checked by the backend. */
export function parseAtomList(text: string): { numbers: number[] } | { error: string } {
  const numbers: number[] = []
  for (const part of text.split(/[\s,;]+/).filter(Boolean)) {
    const range = /^(\d+)\s*[-–]\s*(\d+)$/.exec(part)
    if (range) {
      const [from, to] = [Number(range[1]), Number(range[2])]
      if (from < 1 || to < from) return { error: `“${part}” is not a range of atom numbers` }
      for (let n = from; n <= to; n++) numbers.push(n)
    } else if (/^\d+$/.test(part) && Number(part) >= 1) {
      numbers.push(Number(part))
    } else {
      return { error: `“${part}” is not an atom number` }
    }
  }
  return { numbers }
}

/** The shortest text for atom numbers in their order: upward runs of three or more become
 * ranges ([1, 2, 3, 4, 7, 5] → "1-4, 7, 5"). */
export function formatAtomList(numbers: number[]): string {
  const parts: string[] = []
  let i = 0
  while (i < numbers.length) {
    let j = i
    while (j + 1 < numbers.length && numbers[j + 1] === numbers[j] + 1) j++
    if (j - i >= 2) {
      parts.push(`${numbers[i]}-${numbers[j]}`)
      i = j + 1
    } else {
      parts.push(String(numbers[i]))
      i++
    }
  }
  return parts.join(', ')
}
