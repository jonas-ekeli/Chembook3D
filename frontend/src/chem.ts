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

/** Atoms turned so the molecule's two longest directions lie in the picture plane. */
export function project(atoms: Atom[]): Projected[] {
  if (atoms.length === 0) return []
  const n = atoms.length
  const centre = [0, 0, 0]
  for (const a of atoms) {
    centre[0] += a.x / n
    centre[1] += a.y / n
    centre[2] += a.z / n
  }
  const pts = atoms.map((a) => [a.x - centre[0], a.y - centre[1], a.z - centre[2]])
  const cov = [0, 1, 2].map((i) => [0, 1, 2].map((j) => pts.reduce((sum, p) => sum + p[i] * p[j], 0)))
  const [u, w, d] = principalAxes(cov)
  const dot = (p: number[], axis: Vec) => p[0] * axis[0] + p[1] * axis[1] + p[2] * axis[2]
  return atoms.map((a, i) => ({ element: a.element, x: dot(pts[i], u), y: dot(pts[i], w), depth: dot(pts[i], d) }))
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
