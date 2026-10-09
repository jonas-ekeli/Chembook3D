import { useState } from 'react'
import type { ScanPathPlan, ScanPathSpecies } from '../api'
import { parseXyz } from '../chem'
import { Viewer3D } from './Viewer3D'

/** D120: a free species joining or leaving along a scan path. The app builds the separated
 * structure by pulling the species straight out of the bound structure; it is shown beside the
 * bound one, and its closest contact can be set farther out. */
export function SpeciesSection({
  species,
  plan,
  clearance,
  onClearance,
}: {
  species: ScanPathSpecies
  plan: ScanPathPlan
  clearance: number
  onClearance: (value: number) => void
}) {
  const [typed, setTyped] = useState(String(clearance))
  const atoms = parseXyz(species.bound_xyz)
  const name = (n: number) => `${atoms[n - 1]?.element ?? ''}${n}`
  const joins = species.direction === 'joins'
  const bound = joins ? plan.end.label : plan.start.label
  const commit = () => {
    const value = Number(typed)
    if (Number.isFinite(value) && value >= 3 && value <= 12) onClearance(value)
    else setTyped(String(clearance))
  }
  const highlight = species.atoms.map((n) => ({ index: n - 1, text: String(n) }))
  return (
    <section className="scan-path-section" aria-label="Species">
      <p aria-label="Species summary">
        “{species.label}” {joins ? 'joins' : 'leaves'}: the path {joins ? 'starts' : 'ends'} with it apart, built from “{bound}”: its own geometry
        placed where it binds, then pulled straight out along the line from {species.anchor.map(name).join(', ')} through its centre until it is{' '}
        {species.closest.toFixed(1)} Å from the rest ({species.pull.toFixed(1)} Å out). So it {joins ? 'comes in' : 'leaves'} on the face it binds to,
        turned the way it binds{species.bonds.length > 0 && <> ({species.bonds.map(([a, b]) => `${name(a)}–${name(b)}`).join(', ')})</>}.
      </p>
      <div className="atom-match-views">
        <div>
          <h3>Apart{joins ? ' (the start)' : ' (the end)'}</h3>
          <Viewer3D models={[{ xyz: species.separated_xyz }]} marks={{ labels: highlight }} />
        </div>
        <div>
          <h3>Bound: {bound}</h3>
          <Viewer3D models={[{ xyz: species.bound_xyz }]} marks={{ labels: highlight }} />
        </div>
      </div>
      <label className="field scan-path-add">
        <span className="small">Closest contact apart (Å, 3 to 12)</span>
        <input
          aria-label="Closest contact"
          type="number"
          min={3}
          max={12}
          step={0.5}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          onBlur={commit}
          onKeyDown={(event) => event.key === 'Enter' && commit()}
        />
      </label>
    </section>
  )
}
