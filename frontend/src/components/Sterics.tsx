import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import {
  api,
  type Node,
  type StericAtoms,
  type StericComputed,
  type StericMap,
  type StericProfile,
  type StericProfileFields,
  type StericRadii,
  type StericValues,
} from '../api'
import { formatAtomList, fragment, parseAtomList, parseXyz } from '../chem'
import { download } from '../util'
import { Modal } from './Modal'
import { Viewer3D } from './Viewer3D'

// Buried volume and steric maps (D81, A32). The backend computes everything; these
// components choose a profile, edit its atoms and draw what comes back.

const QUADRANTS = ['NW', 'NE', 'SW', 'SE'] // in the map's layout: north (+y) up, east (+x) right
const OCTANT_ORDER = ['NE', 'NW', 'SW', 'SE']
/** A31's limit for overlays serves here too: more maps side by side are hard to read. */
export const MAX_MAPS = 12

const RADII_NAMES: Record<StericRadii, string> = { bondi: 'Bondi', crc: 'CRC' }

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const nodeName = (n: Node) => n.label || 'Untitled node'
const percent = (value: number | null | undefined) => (value === null || value === undefined ? '—' : value.toFixed(1))

/** "3.5 Å sphere, Bondi ×1.17, hydrogens left out, 0.1 Å mesh": every value is shown with
 * its settings (B2). */
function describeProfile(p: StericProfile): string {
  return `${p.radius} Å sphere, ${RADII_NAMES[p.radii]} radii ×${p.radii_scale}, hydrogens ${p.include_hydrogens ? 'included' : 'left out'}, ${p.mesh} Å mesh`
}

const elementsOf = (n: Node) => parseXyz(n.xyz ?? '').map((a) => a.element).join(' ')

// ---------- colours ----------

// Sequential: one hue, light (low, far below the centre) to dark (high, crowding the centre).
const SEQUENTIAL = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b']
// Diverging: blue where the first map is lower, red where it is higher, grey for no change.
const DIVERGING = ['#0d366b', '#1c5cab', '#3987e5', '#86b6ef', '#cde2fb', '#f0efec', '#f9d3cc', '#f0a091', '#e0644f', '#b8321f', '#7f1d12']

function hex(colour: string): [number, number, number] {
  return [1, 3, 5].map((i) => Number.parseInt(colour.slice(i, i + 2), 16)) as [number, number, number]
}

function ramp(stops: string[], t: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, t))
  const position = clamped * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(position))
  const f = position - i
  const [a, b] = [hex(stops[i]), hex(stops[i + 1])]
  return [0, 1, 2].map((k) => Math.round(a[k] + (b[k] - a[k]) * f)) as [number, number, number]
}

// ---------- the map ----------

const CONTOUR_STEP = 0.5 // Å between contour lines

/** Contour lines every 0.5 Å (marching squares over the grid's cell centres), the z = 0 line
 * darker, so a map's shape reads without telling colours apart. */
function contours(context: CanvasRenderingContext2D, map: StericMap, size: number, cell: number) {
  const n = map.x.length
  const at = (i: number, j: number): [number, number] => [(i + 0.5) * cell, size - (j + 0.5) * cell]
  const first = Math.ceil(-map.limit / CONTOUR_STEP)
  const last = Math.floor(map.limit / CONTOUR_STEP)
  for (let k = first; k <= last; k++) {
    const level = k * CONTOUR_STEP
    context.strokeStyle = k === 0 ? 'rgb(16 24 40 / 0.6)' : 'rgb(16 24 40 / 0.22)'
    context.lineWidth = k === 0 ? 1.2 : 0.8
    context.beginPath()
    for (let j = 0; j < n - 1; j++) {
      for (let i = 0; i < n - 1; i++) {
        const corners: [number, number, number | null][] = [
          [i, j, map.z[j][i]],
          [i + 1, j, map.z[j][i + 1]],
          [i + 1, j + 1, map.z[j + 1][i + 1]],
          [i, j + 1, map.z[j + 1][i]],
        ]
        if (corners.some(([, , z]) => z === null)) continue
        const points: [number, number][] = []
        for (let e = 0; e < 4; e++) {
          const [ia, ja, za] = corners[e] as [number, number, number]
          const [ib, jb, zb] = corners[(e + 1) % 4] as [number, number, number]
          if ((za - level) * (zb - level) >= 0 || za === zb) continue
          const t = (level - za) / (zb - za)
          points.push(at(ia + (ib - ia) * t, ja + (jb - ja) * t))
        }
        for (let p = 0; p + 1 < points.length; p += 2) {
          context.moveTo(...points[p])
          context.lineTo(...points[p + 1])
        }
      }
    }
    context.stroke()
  }
}

const BAR = 46 // room under the map for the colour bar

/** A steric map on a fixed colour scale (−limit to +limit Å), so maps of the same profile
 * compare directly (B2). Drawn on a canvas, which also gives the saved image. */
export function StericMapView({
  map,
  title,
  difference = false,
  size = 240,
  fileName,
}: {
  map: StericMap
  title: string
  difference?: boolean
  size?: number
  fileName?: string
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [hover, setHover] = useState<string | null>(null)
  const n = map.x.length
  const r = map.x[n - 1]

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context) return
    const scale = window.devicePixelRatio || 1
    element.width = size * scale
    element.height = (size + BAR) * scale
    context.setTransform(scale, 0, 0, scale, 0, 0)
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, size, size + BAR)
    const cell = size / n
    const stops = difference ? DIVERGING : SEQUENTIAL
    map.z.forEach((row, j) =>
      row.forEach((z, i) => {
        if (z === null) return
        const [red, green, blue] = ramp(stops, (z + map.limit) / (2 * map.limit))
        context.fillStyle = `rgb(${red} ${green} ${blue})`
        // Row 0 is y = −r: drawn at the bottom, so north (+y) is up.
        context.fillRect(i * cell, size - (j + 1) * cell, cell + 0.5, cell + 0.5)
      }),
    )
    contours(context, map, size, cell)
    const half = size / 2
    context.strokeStyle = '#667085'
    context.lineWidth = 1
    context.beginPath()
    context.arc(half, half, half - 0.5, 0, 2 * Math.PI)
    context.stroke()
    context.setLineDash([3, 3])
    context.beginPath()
    context.moveTo(half, 0)
    context.lineTo(half, size)
    context.moveTo(0, half)
    context.lineTo(size, half)
    context.stroke()
    context.setLineDash([])
    context.fillStyle = '#667085'
    context.font = '11px system-ui, sans-serif'
    context.textBaseline = 'top'
    context.textAlign = 'left'
    context.fillText('NW', 4, 4)
    context.fillText('SW', 4, size - 16)
    context.textAlign = 'right'
    context.fillText('NE', size - 4, 4)
    context.fillText('SE', size - 4, size - 16)
    // Colour bar with its scale in Å.
    const barTop = size + 10
    for (let x = 0; x < size - 40; x++) {
      const [red, green, blue] = ramp(stops, x / (size - 41))
      context.fillStyle = `rgb(${red} ${green} ${blue})`
      context.fillRect(20 + x, barTop, 1.5, 10)
    }
    context.fillStyle = '#1c2430'
    context.textAlign = 'center'
    const limit = map.limit
    context.fillText(`${-limit}`, 20, barTop + 13)
    context.fillText('0', size / 2, barTop + 13)
    context.fillText(`+${limit}`, size - 20, barTop + 13)
    context.fillText(difference ? 'Δz (Å)' : 'z (Å)', size / 2, barTop + 24)
  }, [map, size, n, difference])

  const onMove = (event: MouseEvent<HTMLCanvasElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const px = event.clientX - box.left
    const py = event.clientY - box.top
    if (py > size) {
      setHover(null)
      return
    }
    const i = Math.min(n - 1, Math.max(0, Math.floor((px / size) * n)))
    const j = Math.min(n - 1, Math.max(0, n - 1 - Math.floor((py / size) * n)))
    const z = map.z[j]?.[i] ?? null
    if (Math.hypot(map.x[i], map.x[j]) > r) {
      setHover(null)
      return
    }
    const label = difference ? 'Δz' : 'z'
    setHover(`x ${map.x[i].toFixed(1)}, y ${map.x[j].toFixed(1)} Å: ${z === null ? 'no surface' : `${label} ${z.toFixed(2)} Å`}`)
  }

  return (
    <figure className="steric-map" aria-label={`Steric map ${title}`}>
      <figcaption>{title}</figcaption>
      <canvas
        ref={canvas}
        style={{ width: size, height: size + BAR }}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`${difference ? 'Difference map' : 'Steric map'} of ${title}`}
      />
      <div className="steric-map-foot">
        <span className="muted small mono" aria-live="polite">
          {hover ?? ' '}
        </span>
        {fileName && (
          <button className="small" onClick={() => canvas.current && download(canvas.current.toDataURL('image/png'), fileName)}>
            Save image
          </button>
        )}
      </div>
    </figure>
  )
}

// ---------- values ----------

function QuadrantGrid({ values }: { values: StericValues }) {
  if (!values.quadrants) return null
  return (
    <table className="steric-quadrants" aria-label="Quadrants">
      <tbody>
        {[QUADRANTS.slice(0, 2), QUADRANTS.slice(2)].map((row) => (
          <tr key={row.join()}>
            {row.map((q) => (
              <td key={q}>
                <span className="muted small">{q}</span> <strong className="mono">{percent(values.quadrants![q])}</strong>
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function OctantTable({ values }: { values: StericValues }) {
  if (!values.octants) return null
  return (
    <table className="steric-octants" aria-label="Octants">
      <thead>
        <tr>
          <th />
          {OCTANT_ORDER.map((q) => (
            <th key={q}>{q}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[
          ['+', 'z > 0 (towards the centre’s other side)'],
          ['-', 'z < 0 (the ligand’s side)'],
        ].map(([side, title]) => (
          <tr key={side}>
            <th title={title}>{side === '+' ? '+z' : '−z'}</th>
            {OCTANT_ORDER.map((q) => (
              <td key={q} className="mono">
                {percent(values.octants![`${q}${side}`])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ---------- settings ----------

type SettingsDraft = {
  name: string
  radius: string
  radii: StericRadii
  radii_scale: string
  include_hydrogens: boolean
  mesh: string
  map_limit: string
}

/** B2: a profile's name and parameters. Creating starts from the SambVca defaults. */
export function StericSettingsDialog({
  profile,
  onClose,
  onSaved,
  onDeleted,
}: {
  profile: StericProfile | null
  onClose: () => void
  onSaved: (profile: StericProfile) => void
  onDeleted?: () => void
}) {
  const [draft, setDraft] = useState<SettingsDraft>({
    name: profile?.name ?? '',
    radius: String(profile?.radius ?? 3.5),
    radii: profile?.radii ?? 'bondi',
    radii_scale: String(profile?.radii_scale ?? 1.17),
    include_hydrogens: profile?.include_hydrogens ?? false,
    mesh: String(profile?.mesh ?? 0.1),
    map_limit: profile?.map_limit === null || profile?.map_limit === undefined ? '' : String(profile.map_limit),
  })
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const set = (fields: Partial<SettingsDraft>) => setDraft((d) => ({ ...d, ...fields }))

  const save = () => {
    const numbers: Record<string, number> = {}
    for (const [field, label] of [
      ['radius', 'Sphere radius'],
      ['radii_scale', 'Radius scale'],
      ['mesh', 'Mesh'],
    ] as const) {
      const value = Number(draft[field].replace(',', '.'))
      if (!draft[field].trim() || !Number.isFinite(value)) {
        setError(`${label} must be a number.`)
        return
      }
      numbers[field] = value
    }
    const limitText = draft.map_limit.trim().replace(',', '.')
    const limit = limitText ? Number(limitText) : null
    if (limit !== null && !Number.isFinite(limit)) {
      setError('Colour scale must be a number, or empty for the sphere radius.')
      return
    }
    const fields: StericProfileFields = {
      name: draft.name,
      radius: numbers.radius,
      radii: draft.radii,
      radii_scale: numbers.radii_scale,
      include_hydrogens: draft.include_hydrogens,
      mesh: numbers.mesh,
      map_limit: limit,
    }
    const request = profile ? api.updateStericProfile(profile.id, fields) : api.createStericProfile(fields)
    request.then(onSaved, (err: unknown) => setError(errorText(err)))
  }

  return (
    <Modal
      title={profile ? `Steric profile “${profile.name}”` : 'New steric profile'}
      onClose={onClose}
      actions={
        <>
          {profile && onDeleted && (
            <button className="danger" onClick={() => setConfirming(true)}>
              Delete profile…
            </button>
          )}
          <span className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={save}>
            {profile ? 'Save' : 'Create'}
          </button>
        </>
      }
    >
      <div className="fields">
        <label className="field">
          <span>Name</span>
          <input aria-label="Profile name" placeholder="e.g. Ru pocket" value={draft.name} autoFocus onChange={(e) => set({ name: e.target.value })} />
        </label>
        <div className="field-pair">
          <label className="field">
            <span>Sphere radius (Å)</span>
            <input aria-label="Sphere radius" inputMode="decimal" value={draft.radius} onChange={(e) => set({ radius: e.target.value })} />
          </label>
          <label className="field">
            <span>Mesh (Å)</span>
            <input aria-label="Mesh" inputMode="decimal" value={draft.mesh} onChange={(e) => set({ mesh: e.target.value })} />
          </label>
        </div>
        <div className="field-pair">
          <label className="field">
            <span>Radii</span>
            <select aria-label="Radii" value={draft.radii} onChange={(e) => set({ radii: e.target.value as StericRadii })}>
              <option value="bondi">Bondi</option>
              <option value="crc">CRC</option>
            </select>
          </label>
          <label className="field">
            <span>Radius scale</span>
            <input aria-label="Radius scale" inputMode="decimal" value={draft.radii_scale} onChange={(e) => set({ radii_scale: e.target.value })} />
          </label>
        </div>
        <label className="check">
          <input
            type="checkbox"
            aria-label="Include hydrogens"
            checked={draft.include_hydrogens}
            onChange={(e) => set({ include_hydrogens: e.target.checked })}
          />
          <span>Include hydrogens</span>
        </label>
        <label className="field">
          <span>Map colour scale, ± Å (empty: the sphere radius)</span>
          <input aria-label="Map colour scale" inputMode="decimal" value={draft.map_limit} onChange={(e) => set({ map_limit: e.target.value })} />
        </label>
        <p className="muted small">
          SambVca’s standard settings are a 3.5 Å sphere, Bondi radii ×1.17, hydrogens left out and a 0.1 Å mesh. Changing a
          setting marks the profile’s results out of date; the colour scale only changes how maps are drawn.
        </p>
      </div>
      {error && <p role="alert">{error}</p>}
      {confirming && profile && onDeleted && (
        <Modal
          title="Delete steric profile?"
          onClose={() => setConfirming(false)}
          actions={
            <>
              <button onClick={() => setConfirming(false)}>Cancel</button>
              <button
                className="danger"
                onClick={() => api.deleteStericProfile(profile.id).then(onDeleted, (err: unknown) => setError(errorText(err)))}
              >
                Delete
              </button>
            </>
          }
        >
          <p>
            “{profile.name}” and the atoms and results of its {Object.keys(profile.atoms).length} node
            {Object.keys(profile.atoms).length === 1 ? '' : 's'} are deleted. The nodes themselves stay.
          </p>
        </Modal>
      )}
    </Modal>
  )
}

// ---------- a node's atoms ----------

type Field = keyof StericAtoms
const FIELDS: { key: Field; label: string; help: string }[] = [
  { key: 'centre', label: 'Centre', help: 'One atom (the metal), or several for their centroid.' },
  { key: 'z_axis', label: 'z-axis', help: 'Their centre sets the z-axis, e.g. the carbene carbon; it points from them through the centre.' },
  { key: 'xz_plane', label: 'xz-plane', help: 'Their centre lies in the xz-plane at +x, e.g. one nitrogen of the carbene.' },
  { key: 'excluded', label: 'Left out', help: 'Not counted, e.g. the other ligands. A single centre atom is always left out.' },
]

type PickMode = { kind: 'field'; field: Field } | { kind: 'fragment'; picked: number[] } | { kind: 'ligand' }

/** B2, B3: a node's centre, orientation and left-out atoms in one profile, typed or picked in
 * the 3D view. */
export function StericAtomsDialog({
  node,
  profile,
  onClose,
  onSaved,
}: {
  node: Node
  profile: StericProfile
  onClose: () => void
  onSaved: (profile: StericProfile) => void
}) {
  const current = profile.atoms[node.id]
  const [texts, setTexts] = useState<Record<Field, string>>({
    centre: formatAtomList(current?.centre ?? []),
    z_axis: formatAtomList(current?.z_axis ?? []),
    xz_plane: formatAtomList(current?.xz_plane ?? []),
    excluded: formatAtomList(current?.excluded ?? []),
  })
  const [mode, setMode] = useState<PickMode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const atoms = useMemo(() => parseXyz(node.xyz ?? ''), [node.xyz])

  const numbers = (field: Field): number[] => {
    const result = parseAtomList(texts[field])
    return 'numbers' in result ? result.numbers : []
  }

  const highlighted = (() => {
    if (!mode) return []
    if (mode.kind === 'field') return numbers(mode.field).map((n) => n - 1)
    if (mode.kind === 'fragment') return mode.picked
    return []
  })()

  const onToggle = (index: number) => {
    if (!mode) return
    if (mode.kind === 'field') {
      const list = numbers(mode.field)
      const next = list.includes(index + 1) ? list.filter((n) => n !== index + 1) : [...list, index + 1]
      setTexts((t) => ({ ...t, [mode.field]: formatAtomList(next) }))
      return
    }
    if (mode.kind === 'fragment') {
      const picked = [...mode.picked, index]
      if (picked.length < 2) {
        setMode({ kind: 'fragment', picked })
        return
      }
      const [drop, keep] = picked
      const side = fragment(atoms, drop, [keep])
      if (side.includes(keep)) {
        setNotice(`Atoms ${drop + 1} and ${keep + 1} are joined another way too (a ring), so cutting between them leaves nothing apart.`)
      } else {
        const merged = [...new Set([...numbers('excluded'), ...side.map((i) => i + 1)])].sort((a, b) => a - b)
        setTexts((t) => ({ ...t, excluded: formatAtomList(merged) }))
        setNotice(`Left out ${side.length} atom${side.length === 1 ? '' : 's'} on atom ${drop + 1}’s side of the ${atoms[drop].element}${drop + 1}–${atoms[keep].element}${keep + 1} bond.`)
      }
      setMode(null)
      return
    }
    const centre = numbers('centre').map((n) => n - 1)
    if (centre.length === 0) {
      setNotice('Set the centre first.')
      setMode(null)
      return
    }
    const ligand = new Set(fragment(atoms, index, centre))
    const rest = atoms.map((_, i) => i).filter((i) => !ligand.has(i) && !(centre.length === 1 && i === centre[0]))
    setTexts((t) => ({ ...t, excluded: formatAtomList(rest.map((i) => i + 1)) }))
    setNotice(`Kept the ${ligand.size} atoms bonded to atom ${index + 1} away from the centre; left out the other ${rest.length}.`)
    setMode(null)
  }

  const save = () => {
    const lists = {} as StericAtoms
    for (const { key, label } of FIELDS) {
      const result = parseAtomList(texts[key])
      if ('error' in result) {
        setError(`${label}: ${result.error}`)
        return
      }
      lists[key] = result.numbers
    }
    api.updateStericProfile(profile.id, { atoms: { [node.id]: lists } }).then(onSaved, (err: unknown) => setError(errorText(err)))
  }

  const prompt = !mode
    ? null
    : mode.kind === 'field'
      ? `Click the ${FIELDS.find((f) => f.key === mode.field)!.label.toLowerCase()} atoms; click one again to take it out. Press Done when finished.`
      : mode.kind === 'fragment'
        ? mode.picked.length === 0
          ? 'Click an atom on the side to leave out.'
          : 'Now click the atom it is bonded to on the side to keep.'
        : 'Click the ligand’s atom bonded to the centre. Everything else is left out.'

  return (
    <Modal
      title={`Atoms of “${nodeName(node)}” in “${profile.name}”`}
      wide
      onClose={onClose}
      actions={
        <>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!!mode} onClick={save}>
            Save atoms
          </button>
        </>
      }
    >
      <div className="steric-atoms">
        <div className="steric-atoms-lists">
          <p className="muted small">Numbers and ranges such as 1-12, 15, as in the full structure (hidden hydrogens keep their numbers).</p>
          <table className="overlay-lists">
            <tbody>
              {FIELDS.map(({ key, label, help }) => (
                <tr key={key}>
                  <td title={help}>{label}</td>
                  <td>
                    <input
                      aria-label={`${label} atoms`}
                      className="mono"
                      value={texts[key]}
                      onChange={(e) => setTexts((t) => ({ ...t, [key]: e.target.value }))}
                    />
                    <div className="muted small">{help}</div>
                  </td>
                  <td>
                    <button
                      className="small"
                      aria-pressed={mode?.kind === 'field' && mode.field === key}
                      onClick={() => setMode(mode?.kind === 'field' && mode.field === key ? null : { kind: 'field', field: key })}
                    >
                      {mode?.kind === 'field' && mode.field === key ? 'Done' : 'Pick'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="buttons">
            <button
              className="small"
              aria-pressed={mode?.kind === 'fragment'}
              onClick={() => setMode(mode?.kind === 'fragment' ? null : { kind: 'fragment', picked: [] })}
              title="Leave out everything on one side of a bond"
            >
              Leave out a fragment…
            </button>
            <button
              className="small"
              aria-pressed={mode?.kind === 'ligand'}
              onClick={() => setMode(mode?.kind === 'ligand' ? null : { kind: 'ligand' })}
              title="One ligand's buried volume, as in SambVca: leave out all other atoms"
            >
              Keep only one ligand…
            </button>
            <button className="small" onClick={() => setTexts((t) => ({ ...t, excluded: '' }))}>
              Clear left out
            </button>
          </div>
          <p className="muted small">
            Fragments are found from bonds by distance. Without z-axis and xz-plane atoms only the total %V_bur is computed;
            quadrants, octants and the map need both.
          </p>
          {prompt && (
            <p className="muted small" aria-label="Picking">
              {prompt}
            </p>
          )}
          {notice && <p className="muted small">{notice}</p>}
          {error && <p role="alert">{error}</p>}
        </div>
        <div className="steric-atoms-viewer">
          <Viewer3D models={[{ xyz: node.xyz ?? '' }]} picking={mode ? { atoms: highlighted, onToggle } : undefined} />
        </div>
      </div>
    </Modal>
  )
}

// ---------- the node inspector's section (B4) ----------

/** The profile to show first: the last one used, if it still exists. */
let lastProfileId: string | null = null

export function StericsSection({ node, nodes }: { node: Node; nodes: Node[] }) {
  const [profiles, setProfiles] = useState<StericProfile[] | null>(null)
  const [profileId, setProfileId] = useState<string | null>(lastProfileId)
  const [computed, setComputed] = useState<StericComputed | null>(null)
  const [editing, setEditing] = useState<'atoms' | 'settings' | 'new' | null>(null)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.stericProfiles().then(setProfiles, (err: unknown) => setError(errorText(err)))
  useEffect(() => {
    void load()
    // New coordinates can put the stored result out of date.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id, node.xyz])

  const profile =
    profiles?.find((p) => p.id === profileId) ?? profiles?.find((p) => node.id in p.atoms) ?? profiles?.[0] ?? null
  const inProfile = !!profile && node.id in profile.atoms
  const stored = profile?.results[node.id] ?? null
  const current = stored && !stored.stale

  // A current result is recomputed (it takes well under a second) to draw its map; an
  // out-of-date one waits for the user (B4).
  const key = profile && current ? `${profile.id}:${stored.computed_at}:${profile.map_limit}` : null
  useEffect(() => {
    setComputed(null)
    if (!profile || !key) return
    let cancelled = false
    api.computeSterics(profile.id, [node.id], true).then(
      ([item]) => !cancelled && setComputed(item),
      (err: unknown) => !cancelled && setError(errorText(err)),
    )
    return () => {
      cancelled = true
    }
    // `key` stands for the profile and the stored result.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, node.id])

  const choose = (id: string) => {
    lastProfileId = id
    setProfileId(id)
    setError(null)
  }

  const replaced = (saved: StericProfile) => {
    setProfiles((list) => [...(list ?? []).filter((p) => p.id !== saved.id), saved].sort((a, b) => a.name.localeCompare(b.name)))
    choose(saved.id)
  }

  // Storing the new result makes it current, and the effect above then draws its map.
  const compute = () => {
    if (!profile) return
    api.computeSterics(profile.id, [node.id]).then(
      ([item]) => {
        setError(item.error)
        if (!item.error) void load()
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const shareFrom = useMemo(() => {
    if (!profile || inProfile) return []
    const mine = elementsOf(node)
    return nodes.filter((n) => n.id !== node.id && n.id in profile.atoms && elementsOf(n) === mine)
  }, [profile, inProfile, node, nodes])

  const values = computed?.values ?? stored?.values ?? null
  const members = profile ? nodes.filter((n) => n.id in profile.atoms) : []

  return (
    <section aria-label="Sterics" className="sterics">
      <div className="section-head">
        <h3>Sterics</h3>
        {profiles && profiles.length > 0 && profile && (
          <select aria-label="Steric profile" value={profile.id} onChange={(e) => choose(e.target.value)}>
            {profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {node.id in p.atoms ? '' : ' (not set up for this node)'}
              </option>
            ))}
          </select>
        )}
        <span className="spacer" />
        {profile && (
          <button className="small" onClick={() => setEditing('settings')} title="The profile's name and settings">
            Settings…
          </button>
        )}
        <button className="small" onClick={() => setEditing('new')} disabled={!node.xyz} title={node.xyz ? undefined : 'No coordinates yet'}>
          New profile…
        </button>
      </div>
      {profiles && profiles.length === 0 && (
        <p className="muted">
          No steric profile yet. A profile holds the settings for buried volume (%V_bur) and steric maps; each node in it
          keeps its own centre, orientation and left-out atoms.
        </p>
      )}
      {profile && <p className="muted small">{describeProfile(profile)}</p>}
      {profile && !inProfile && (
        <div className="buttons">
          <span className="muted">This node is not in “{profile.name}” yet.</span>
          <button className="small primary" disabled={!node.xyz} onClick={() => setEditing('atoms')}>
            Choose its atoms…
          </button>
          {shareFrom.map((n) => (
            <button
              key={n.id}
              className="small"
              title="It has the same elements in the same order, so the same atom numbers apply"
              onClick={() =>
                api.updateStericProfile(profile.id, { atoms: { [node.id]: { same_as: n.id } } }).then(replaced, (err: unknown) => setError(errorText(err)))
              }
            >
              Use the atoms of {nodeName(n)}
            </button>
          ))}
        </div>
      )}
      {profile && inProfile && (
        <>
          <p className="muted small mono">
            Centre {formatAtomList(profile.atoms[node.id].centre)}
            {profile.atoms[node.id].z_axis.length > 0 &&
              ` · z-axis ${formatAtomList(profile.atoms[node.id].z_axis)} · xz-plane ${formatAtomList(profile.atoms[node.id].xz_plane)}`}
            {profile.atoms[node.id].excluded.length > 0 && ` · left out ${formatAtomList(profile.atoms[node.id].excluded)}`}
          </p>
          <div className="buttons">
            <button className="small" onClick={() => setEditing('atoms')}>
              Edit atoms…
            </button>
            <button className="small" onClick={compute} disabled={!!current}>
              {stored ? 'Recompute' : 'Compute'}
            </button>
            <button className="small" disabled={members.length < 2} onClick={() => setComparing(true)} title="All nodes in this profile">
              Compare nodes in profile…
            </button>
          </div>
          {stored?.stale && (
            <p className="stale small" aria-label="Out of date">
              Out of date: {stored.stale}. Recompute to update.
            </p>
          )}
          {!stored && !computed && <p className="muted">Not computed yet.</p>}
          {values && (
            <div className={`steric-result${stored?.stale && !computed ? ' is-stale' : ''}`} aria-label="Buried volume">
              <div>
                <div className="steric-total">
                  %V_bur <strong className="mono">{percent(values.buried_percent)}</strong>
                </div>
                <QuadrantGrid values={values} />
                <OctantTable values={values} />
                {!values.quadrants && (
                  <p className="muted small">Choose z-axis and xz-plane atoms for quadrants, octants and the map.</p>
                )}
                {values.missing_radii.length > 0 && (
                  <p className="muted small">
                    No {RADII_NAMES[profile.radii]} radius for {values.missing_radii.join(', ')}; 2.0 Å (scaled) was used.
                  </p>
                )}
              </div>
              {computed?.map && (
                <StericMapView map={computed.map} title={nodeName(node)} fileName={`${nodeName(node)} steric map.png`} />
              )}
            </div>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {editing === 'atoms' && profile && (
        <StericAtomsDialog
          node={node}
          profile={profile}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(null)
            replaced(saved)
          }}
        />
      )}
      {(editing === 'settings' || editing === 'new') && (
        <StericSettingsDialog
          profile={editing === 'settings' ? profile : null}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            setEditing(editing === 'new' ? 'atoms' : null)
            replaced(saved)
          }}
          onDeleted={() => {
            setEditing(null)
            lastProfileId = null
            setProfileId(null)
            void load()
          }}
        />
      )}
      {comparing && profile && (
        <CompareStericsDialog nodes={members} title={`Nodes in “${profile.name}”`} profileId={profile.id} onClose={() => setComparing(false)} />
      )}
    </section>
  )
}

// ---------- comparison (B5) ----------

/** B5: %V_bur by quadrant for a branch, pathway, group or selection, with the maps side by
 * side on one colour scale and a difference map of two nodes. */
export function CompareStericsDialog({
  nodes,
  title,
  profileId,
  onClose,
}: {
  nodes: Node[]
  title: string
  profileId?: string
  onClose: () => void
}) {
  const [profiles, setProfiles] = useState<StericProfile[] | null>(null)
  const [chosen, setChosen] = useState<string | null>(profileId ?? null)
  const [results, setResults] = useState<StericComputed[] | null>(null)
  const [octants, setOctants] = useState(false)
  const [first, setFirst] = useState('')
  const [second, setSecond] = useState('')
  const [difference, setDifference] = useState<StericMap | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.stericProfiles().then(
      (list) => {
        setProfiles(list)
        if (!chosen && list.length) {
          // The profile holding most of these nodes.
          const count = (p: StericProfile) => nodes.filter((n) => n.id in p.atoms).length
          setChosen([...list].sort((a, b) => count(b) - count(a))[0].id)
        }
      },
      (err: unknown) => setError(errorText(err)),
    )
    // Only on opening.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const profile = profiles?.find((p) => p.id === chosen) ?? null
  const ids = nodes.map((n) => n.id)
  useEffect(() => {
    setResults(null)
    setDifference(null)
    if (!chosen) return
    let cancelled = false
    api.computeSterics(chosen, ids, true).then(
      (items) => {
        if (cancelled) return
        setResults(items)
        setError(null)
        const mapped = items.filter((i) => i.map)
        setFirst(mapped[0]?.node_id ?? '')
        setSecond(mapped[1]?.node_id ?? '')
      },
      (err: unknown) => !cancelled && setError(errorText(err)),
    )
    return () => {
      cancelled = true
    }
    // `ids` is new on every render; the nodes do not change while the dialog is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chosen])

  const withMaps = results?.filter((r) => r.map) ?? []
  const quadrantKeys = ['NE', 'NW', 'SW', 'SE']
  const octantKeys = OCTANT_ORDER.flatMap((q) => [`${q}+`, `${q}-`])

  const exportCsv = () => {
    if (!chosen) return
    api.stericTableCsv(chosen, ids).then(
      (blob) => {
        const url = URL.createObjectURL(blob)
        download(url, 'buried-volume.csv')
        setTimeout(() => URL.revokeObjectURL(url), 1000)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }

  const showDifference = () => {
    if (!chosen || !first || !second) return
    api.stericDifference(chosen, first, second).then(
      (map) => {
        setDifference(map)
        setError(null)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }
  const labelOf = (id: string) => results?.find((r) => r.node_id === id)?.label ?? ''

  return (
    <Modal
      title="Compare sterics"
      wide
      onClose={onClose}
      actions={
        <>
          <button disabled={!results} onClick={exportCsv} title="The values with the settings and atoms on every row">
            Save CSV
          </button>
          <button onClick={onClose}>Close</button>
        </>
      }
    >
      <div className="overlay-options">
        <label className="field">
          <span>Steric profile</span>
          <select aria-label="Steric profile" value={chosen ?? ''} onChange={(e) => setChosen(e.target.value || null)}>
            {!chosen && <option value="">Choose…</option>}
            {profiles?.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={octants} onChange={(e) => setOctants(e.target.checked)} />
          <span>Show octants</span>
        </label>
        <span className="muted small">{title}</span>
      </div>
      {profiles && profiles.length === 0 && <p className="muted">No steric profile yet. Create one in a node’s Sterics section.</p>}
      {profile && <p className="muted small">{describeProfile(profile)}</p>}
      {error && <p role="alert">{error}</p>}
      {results && (
        <table className="overlay-legend steric-table" aria-label="Buried volume table">
          <thead>
            <tr>
              <th>Node</th>
              <th>%V_bur</th>
              {(octants ? octantKeys : quadrantKeys).map((k) => (
                <th key={k}>{k.replace('-', '−')}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {results.map((r) => (
              <tr key={r.node_id}>
                <td>{r.label}</td>
                <td className="mono">{percent(r.values?.buried_percent)}</td>
                {(octants ? octantKeys : quadrantKeys).map((k) => (
                  <td key={k} className="mono">
                    {percent((octants ? r.values?.octants : r.values?.quadrants)?.[k])}
                  </td>
                ))}
                <td className="muted small">{r.error ?? (r.values && !r.values.quadrants ? 'no orientation' : '')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {withMaps.length > 0 && (
        <>
          <div className="steric-maps" aria-label="Steric maps">
            {withMaps.slice(0, MAX_MAPS).map((r) => (
              <StericMapView key={r.node_id} map={r.map!} title={r.label} size={200} fileName={`${r.label} steric map.png`} />
            ))}
          </div>
          {withMaps.length > MAX_MAPS && (
            <p className="muted small">The first {MAX_MAPS} maps are shown; the table and CSV hold every node.</p>
          )}
          {withMaps.length >= 2 && (
            <div className="overlay-options" aria-label="Difference map">
              <label className="field">
                <span>Difference map</span>
                <select aria-label="First map" value={first} onChange={(e) => setFirst(e.target.value)}>
                  {withMaps.map((r) => (
                    <option key={r.node_id} value={r.node_id}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>minus</span>
                <select aria-label="Second map" value={second} onChange={(e) => setSecond(e.target.value)}>
                  {withMaps.map((r) => (
                    <option key={r.node_id} value={r.node_id}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <button className="small" disabled={!first || !second || first === second} onClick={showDifference}>
                Show difference
              </button>
            </div>
          )}
          {difference && (
            <>
              <StericMapView
                map={difference}
                title={`${labelOf(first)} − ${labelOf(second)}`}
                difference
                fileName="steric difference map.png"
              />
              <p className="muted small">Red where the first rises higher (more crowded towards the centre), blue where it lies lower.</p>
            </>
          )}
        </>
      )}
    </Modal>
  )
}
