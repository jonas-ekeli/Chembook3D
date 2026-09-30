import { memo, useContext, useMemo } from 'react'
import { bonds, elementColour, hiddenAtoms, parseXyz, project, type Rotation } from '../chem'
import { HydrogenDisplay } from '../display'

/** Structure view mode (D43): a small ball-and-stick picture of a node's geometry, drawn as
 * SVG so hundreds of nodes stay cheap and the canvas image export includes it. It is turned
 * the way the node's 3D view was saved (`rotation`), or by the default orientation, and
 * leaves out the hydrogens the hydrogens setting hides. */
export const MoleculeSketch = memo(function MoleculeSketch({
  xyz,
  rotation = null,
  width = 150,
  height = 110,
}: {
  xyz: string | null
  rotation?: Rotation | null
  width?: number
  height?: number
}) {
  const hydrogens = useContext(HydrogenDisplay)
  const drawing = useMemo(() => {
    if (!xyz) return null
    const atoms = parseXyz(xyz)
    if (atoms.length === 0) return null
    // The orientation comes from all atoms, so hiding hydrogens does not turn the picture.
    const points = project(atoms, rotation)
    const allPairs = bonds(atoms)
    const hidden = hiddenAtoms(atoms, hydrogens, allPairs)
    const shown = atoms.map((_, i) => i).filter((i) => !hidden.has(i))
    if (shown.length === 0) return null
    const xs = shown.map((i) => points[i].x)
    const ys = shown.map((i) => points[i].y)
    const spanX = Math.max(...xs) - Math.min(...xs) || 1
    const spanY = Math.max(...ys) - Math.min(...ys) || 1
    const margin = 8
    const scale = Math.min((width - 2 * margin) / spanX, (height - 2 * margin) / spanY, 40)
    const cx = (Math.max(...xs) + Math.min(...xs)) / 2
    const cy = (Math.max(...ys) + Math.min(...ys)) / 2
    const at = (i: number) => ({
      x: width / 2 + (points[i].x - cx) * scale,
      y: height / 2 - (points[i].y - cy) * scale,
    })
    const radius = Math.max(1.2, Math.min(5, scale * 0.28))
    const order = shown.sort((a, b) => points[a].depth - points[b].depth)
    const pairs = allPairs.filter(([i, j]) => !hidden.has(i) && !hidden.has(j))
    return { atoms, points, at, radius, order, pairs }
  }, [xyz, rotation, hydrogens, width, height])

  if (!drawing) {
    return (
      <svg width={width} height={height} className="sketch empty" role="img" aria-label="No coordinates">
        <text x={width / 2} y={height / 2} textAnchor="middle" dominantBaseline="middle">
          no coordinates
        </text>
      </svg>
    )
  }
  const { atoms, at, radius, order, pairs } = drawing
  return (
    <svg width={width} height={height} className="sketch" role="img" aria-label="Structure">
      {pairs.map(([i, j]) => {
        const a = at(i)
        const b = at(j)
        const light = atoms[i].element === 'H' || atoms[j].element === 'H'
        return (
          <line
            key={`${i}-${j}`}
            x1={a.x}
            y1={a.y}
            x2={b.x}
            y2={b.y}
            stroke={light ? '#c4c8cf' : '#5f6670'}
            strokeWidth={light ? 0.8 : 1.4}
          />
        )
      })}
      {order.map((i) => {
        const p = at(i)
        const hydrogen = atoms[i].element === 'H'
        return (
          <circle
            key={i}
            cx={p.x}
            cy={p.y}
            r={hydrogen ? radius * 0.55 : atoms[i].element === 'C' ? radius * 0.8 : radius}
            fill={elementColour(atoms[i].element)}
            stroke="#3a3f47"
            strokeWidth={hydrogen ? 0.2 : 0.4}
          />
        )
      })}
    </svg>
  )
})
