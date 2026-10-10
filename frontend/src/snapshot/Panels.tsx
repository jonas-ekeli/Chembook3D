// The side panel of the read-only copy (D79): what the app's inspectors show, as text, with
// nothing that edits. Links select other records, as in the app.

import { useState } from 'react'
import Markdown from 'react-markdown'
import {
  CALCULATION_TYPES,
  formatDelta,
  pathMissed,
  pathTop,
  ROLES,
  speciesChip,
  STATUS_LABEL,
  type Branch,
  type Canvas,
  type EnergyView,
  type Group,
  type Node,
  type Settings,
  type Transition,
} from '../api'
import { ResultValues } from '../components/CalculationList'
import { OverviewSections } from '../components/Overview'
import { PinnedNotesSection } from '../components/NoteContent'
import { PopOut, PopOutButton } from '../components/PopOut'
import { usePopOut } from '../popOut'
import { Viewer3D } from '../components/Viewer3D'
import { download, fileName, hartree, withDisplacements } from '../util'
import type { SharedCalculation, SnapshotData } from './data'

type Select = {
  onSelectNode: (id: string) => void
  onSelectGroup: (id: string) => void
  onSelectBranch: (id: string) => void
  onSelectTransition: (id: string) => void
}

const nodeName = (n: Node | undefined) =>
  n ? n.label || (n.kind === 'species' ? 'Untitled species' : 'Untitled node') : 'a deleted node'
const branchName = (b: Branch | undefined) => (b ? b.name || 'Unnamed branch' : '—')

function NotesView({ notes }: { notes: string }) {
  return (
    <section aria-label="Notes">
      <h3>Notes</h3>
      {notes.trim() ? (
        <div className="markdown">
          <Markdown>{notes}</Markdown>
        </div>
      ) : (
        <p className="muted">No notes.</p>
      )}
    </section>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </>
  )
}

function EndLink({ id, canvas, select }: { id: string; canvas: Canvas; select: Select }) {
  const node = canvas.nodes.find((n) => n.id === id)
  if (node) {
    return (
      <button className="link" onClick={() => select.onSelectNode(id)}>
        {nodeName(node)}
      </button>
    )
  }
  const group = canvas.groups.find((g) => g.id === id)
  return group ? (
    <button className="link" onClick={() => select.onSelectGroup(id)}>
      {group.label || 'Group'} (group)
    </button>
  ) : (
    <em>deleted</em>
  )
}

/** FR-3D-03 in the copy: the modes it carries (A30), imaginary first. */
function SharedVibrations({
  calculations,
  modes,
  onChange,
}: {
  calculations: SharedCalculation[]
  modes: SnapshotData['modes']
  onChange: (vibration: { xyz: string } | null) => void
}) {
  const withModes = calculations.filter((c) => modes[c.id])
  const [calcId, setCalcId] = useState(withModes.at(-1)?.id ?? '')
  const [mode, setMode] = useState('')
  const found = modes[calcId]
  if (!found) return null
  return (
    <div className="vibrations" aria-label="Vibrations">
      {withModes.length > 1 && (
        <select
          aria-label="Frequency calculation"
          value={calcId}
          onChange={(event) => {
            setCalcId(event.target.value)
            setMode('')
            onChange(null)
          }}
        >
          {withModes.map((c) => (
            <option key={c.id} value={c.id}>
              {c.composite_label}
            </option>
          ))}
        </select>
      )}
      <select
        aria-label="Animate mode"
        value={mode}
        onChange={(event) => {
          setMode(event.target.value)
          const index = event.target.value === '' ? -1 : Number(event.target.value)
          onChange(index >= 0 ? { xyz: withDisplacements(found.xyz, found.modes[index]) } : null)
        }}
      >
        <option value="">No animation</option>
        {found.order.map((index) => (
          <option key={index} value={index}>
            Mode {index + 1}:{' '}
            {found.frequencies[index] < 0
              ? `${Math.abs(found.frequencies[index]).toFixed(1)}i`
              : found.frequencies[index].toFixed(1)}{' '}
            cm⁻¹
          </option>
        ))}
      </select>
      <span className="muted small">Imaginary and lowest modes only in this copy.</span>
    </div>
  )
}

function Calculations({ calculations }: { calculations: SharedCalculation[] }) {
  const [open, setOpen] = useState<string | null>(null)
  if (calculations.length === 0) return <p className="muted">No calculations.</p>
  return (
    <ul className="calculations" aria-label="Calculations">
      {calculations.map((c) => (
        <li key={c.id}>
          <button className="calc-row" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
            <span className="calc-type">{CALCULATION_TYPES[c.type] ?? c.type}</span>
            <span className="calc-level">
              {c.composite_label}
              {c.level_edited && <span className="badge">edited</span>}
            </span>
            <span className="mono">{hartree(c.result?.energy, 8)}</span>
            {c.result?.g !== null && c.result?.g !== undefined && <span className="mono">G {hartree(c.result.g)}</span>}
            {c.result?.imaginary_count !== null && c.result?.imaginary_count !== undefined && (
              <span>{c.result.imaginary_count} imag.</span>
            )}
            {c.warnings.map((w) => (
              <span key={w.code} className="badge warn" title={w.message}>
                {w.code}
              </span>
            ))}
          </button>
          {open === c.id && (
            <div className="calc-details">
              <p className="muted small">
                {c.program} {c.program_version}
                {c.step_index && c.step_count && (
                  <> · step {c.step_index} of {c.step_count}</>
                )}
                {c.title && <> · “{c.title}”</>}
              </p>
              {c.route && <pre className="route">{c.route}</pre>}
              <ResultValues calculation={c} />
              <dl className="values">
                <Fact label="Level of theory">{c.level?.label || '—'}</Fact>
                {c.geometry_level && <Fact label="Geometry level">{c.geometry_level.label}</Fact>}
                {c.source_file && (
                  <Fact label="Imported from">
                    {c.source_file.original_name}{' '}
                    <span className="muted small">
                      ({(c.source_file.size / 1024).toFixed(0)} KB, SHA-256{' '}
                      <code title={c.source_file.checksum}>{c.source_file.checksum.slice(0, 12)}…</code>)
                    </span>
                  </Fact>
                )}
              </dl>
              {c.notes && <p className="notes-text">{c.notes}</p>}
            </div>
          )}
        </li>
      ))}
    </ul>
  )
}

export function NodePanel({
  node,
  data,
  select,
  isReference,
  canBeReference,
  onUseAsReference,
}: {
  node: Node
  data: SnapshotData
  select: Select
  isReference: boolean
  canBeReference: boolean
  onUseAsReference: () => void
}) {
  const [vibration, setVibration] = useState<{ xyz: string } | null>(null)
  const [poppedOut, setPoppedOut] = usePopOut()
  const canvas = data.canvas
  const isSpecies = node.kind === 'species'
  const nodes = [...canvas.nodes, ...canvas.species]
  const parent = node.derived_from_id ? nodes.find((n) => n.id === node.derived_from_id) : undefined
  const children = nodes.filter((n) => n.derived_from_id === node.id)
  const branch = canvas.branches.find((b) => b.id === node.branch_id)
  const group = canvas.groups.find((g) => g.id === node.group_id)
  const step = canvas.steps.find((s) => s.id === node.step_id)
  const edges = canvas.transitions.filter((t) => t.source_id === node.id || t.target_id === node.id)
  const carriedBy = canvas.transitions.filter((t) => t.species.some((s) => s.species_id === node.id))
  const calculations = data.calculations[node.id] ?? []
  const endName = (id: string) => {
    const other = nodes.find((n) => n.id === id)
    if (other) return nodeName(other)
    const g = canvas.groups.find((x) => x.id === id)
    return g ? `${g.label || 'Group'} (group)` : 'deleted'
  }
  const saveXyz = () => {
    if (!node.xyz) return
    const url = URL.createObjectURL(new Blob([node.xyz], { type: 'chemical/x-xyz' }))
    download(url, `${fileName(node.label, 'structure')}.xyz`)
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className="inspector" aria-label="Node inspector">
      <div className="inspector-head">
        <h2>{nodeName(node)}</h2>
        {isSpecies && <span className="badge species">Free species</span>}
        <span className="spacer" />
        {!isSpecies && (
          <button
            onClick={onUseAsReference}
            disabled={isReference || !canBeReference}
            title={
              canBeReference
                ? 'Energy mode and profiles show values relative to this node (EN-8)'
                : 'This copy has energies relative to the nodes on its pathways only'
            }
          >
            {isReference ? 'Energy reference' : 'Use as energy reference'}
          </button>
        )}
        {node.xyz && <button onClick={saveXyz}>Save .xyz</button>}
      </div>
      {node.warnings.length > 0 && (
        <ul className="warnings" aria-label="Warnings">
          {node.warnings.map((w, i) => (
            <li key={i}>
              <span className="badge warn">{w.code}</span> {w.message}
            </li>
          ))}
        </ul>
      )}
      <div className="facts">
        <span>
          Formula: <strong>{node.formula ?? '—'}</strong>
        </span>
        <span>{node.atom_count} atoms</span>
        <span>
          {node.calculation_count} calculation{node.calculation_count === 1 ? '' : 's'}
        </span>
        {node.derived_from_id && (
          <span>
            Derived from{' '}
            {parent ? (
              <button className="link" onClick={() => select.onSelectNode(parent.id)}>
                {nodeName(parent)}
              </button>
            ) : (
              <em>a deleted node</em>
            )}
          </span>
        )}
        {children.length > 0 && (
          <span>
            Derived nodes:{' '}
            {children.map((child, i) => (
              <span key={child.id}>
                {i > 0 && ', '}
                <button className="link" onClick={() => select.onSelectNode(child.id)}>
                  {nodeName(child)}
                </button>
              </span>
            ))}
          </span>
        )}
        {group && (
          <span>
            Member of{' '}
            <button className="link" onClick={() => select.onSelectGroup(group.id)}>
              {group.label || 'a group'}
            </button>
            {group.representative_id === node.id && <strong> (representative)</strong>}
          </span>
        )}
      </div>

      <div className="inspector-grid">
        <div className="column">
          <dl className="values" aria-label="Details">
            <Fact label="Status">
              <span className={`status status-${node.status}`}>{STATUS_LABEL[node.status]}</span>
            </Fact>
            {!isSpecies && <Fact label="Role">{ROLES.find((r) => r.value === node.role)?.label}</Fact>}
            {!isSpecies && <Fact label="Step">{step ? step.name || 'Unnamed step' : 'No step'}</Fact>}
            {!isSpecies && (
              <Fact label="Branch">
                {branch ? (
                  <button className="link" onClick={() => select.onSelectBranch(branch.id)}>
                    <span className="swatch" style={{ background: branch.colour }} /> {branchName(branch)}
                  </button>
                ) : (
                  'No branch'
                )}
              </Fact>
            )}
            <Fact label="Charge, multiplicity">
              {node.charge ?? '—'}, {node.multiplicity ?? '—'}
            </Fact>
            <Fact label="Tags">
              {node.tags.length ? (
                <span className="tags">
                  {node.tags.map((tag) => (
                    <span key={tag} className="tag">
                      {tag}
                    </span>
                  ))}
                </span>
              ) : (
                '—'
              )}
            </Fact>
          </dl>
          <NotesView notes={node.notes} />
          <PinnedNotesSection notes={canvas.notes.filter((n) => n.node_id === node.id)} />
        </div>
        <div className="column">
          <PopOut popped={poppedOut} title={`3D view: ${nodeName(node)}`} onDock={() => setPoppedOut(false)}>
            <Viewer3D
              models={node.xyz ? [{ xyz: node.xyz }] : []}
              vibration={vibration}
              rotation={node.view_rotation}
              corner={<PopOutButton popped={poppedOut} onClick={() => setPoppedOut(!poppedOut)} />}
            />
          </PopOut>
          <SharedVibrations key={node.id} calculations={calculations} modes={data.modes} onChange={setVibration} />
          {node.xyz && (
            <details className="xyz-text">
              <summary>Coordinates (xyz)</summary>
              <pre className="mono">{node.xyz}</pre>
            </details>
          )}
        </div>
      </div>

      <section aria-label="Transitions">
        <h3>{isSpecies ? 'Joins or leaves on' : 'Transitions'}</h3>
        {(isSpecies ? carriedBy : edges).length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <ul className="plain transitions">
            {isSpecies
              ? carriedBy.map((t) => {
                  const entry = t.species.find((s) => s.species_id === node.id)!
                  return (
                    <li key={t.id}>
                      <button className="link" onClick={() => select.onSelectTransition(t.id)}>
                        {endName(t.source_id)} → {endName(t.target_id)}
                      </button>
                      <span className={`chip species-${entry.direction}`}>{speciesChip(entry)}</span>
                    </li>
                  )
                })
              : edges.map((t) => (
                  <li key={t.id}>
                    <button className="link" onClick={() => select.onSelectTransition(t.id)}>
                      {t.target_id === node.id ? `from ${endName(t.source_id)}` : `to ${endName(t.target_id)}`}
                    </button>
                    {t.direct && <span className="no-ts">no TS</span>}
                    <span className={`status status-${t.status}`}>{STATUS_LABEL[t.status]}</span>
                  </li>
                ))}
          </ul>
        )}
      </section>

      <section aria-label="Calculations section">
        <h3>Calculations</h3>
        <Calculations calculations={calculations} />
      </section>
    </div>
  )
}

export function GroupPanel({
  group,
  canvas,
  select,
  energy,
}: {
  group: Group
  canvas: Canvas
  select: Select
  energy: { view: EnergyView | null; typeName: string; settings: Settings }
}) {
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  const members = group.member_ids.map((id) => canvas.nodes.find((n) => n.id === id)).filter((n): n is Node => !!n)
  const step = canvas.steps.find((s) => s.id === group.step_id)
  const valueOf = (id: string) => energy.view?.values[id]?.value ?? null
  const known = members.map((m) => valueOf(m.id)).filter((v): v is number => v !== null)
  const lowest = known.length ? Math.min(...known) : null
  const outgoing = group.outgoing_branch_id ? byId.get(group.outgoing_branch_id) : undefined
  return (
    <div className="inspector" aria-label="Group inspector">
      <div className="inspector-head">
        <h2>{group.label || 'Group'}</h2>
      </div>
      <dl className="values" aria-label="Details">
        <Fact label="Step">{step ? step.name || 'Unnamed step' : 'No step'}</Fact>
        <Fact label="Outgoing branch">
          {outgoing ? (
            <button className="link" onClick={() => select.onSelectBranch(outgoing.id)}>
              {branchName(outgoing)}
            </button>
          ) : (
            'None'
          )}
        </Fact>
        <Fact label="Incoming branches">
          {group.incoming_branch_ids.length
            ? group.incoming_branch_ids.map((id, i) => (
                <span key={id}>
                  {i > 0 && ', '}
                  <button className="link" onClick={() => select.onSelectBranch(id)}>
                    {branchName(byId.get(id))}
                  </button>
                </span>
              ))
            : '—'}
        </Fact>
      </dl>
      <section aria-label="Members">
        <h3>Members ({members.length})</h3>
        <table className="members">
          <thead>
            <tr>
              <th>Node</th>
              <th>Branch</th>
              {energy.view && <th>Δ{energy.typeName} from lowest</th>}
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td>
                  <button className="link" onClick={() => select.onSelectNode(m.id)}>
                    {nodeName(m)}
                  </button>
                  {group.representative_id === m.id && <strong title="Representative"> ★</strong>}
                </td>
                <td className="muted">{m.branch_id ? branchName(byId.get(m.branch_id)) : '—'}</td>
                {energy.view && (
                  <td className="mono" title={energy.view.values[m.id]?.message ?? undefined}>
                    {formatDelta(
                      valueOf(m.id) !== null && lowest !== null ? (valueOf(m.id) as number) - lowest : null,
                      energy.settings,
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">★ marks the representative, the member the outgoing path continues from (D18).</p>
      </section>
      <NotesView notes={group.notes} />
    </div>
  )
}

export function TransitionPanel({
  transition,
  canvas,
  select,
  settings,
}: {
  transition: Transition
  canvas: Canvas
  select: Select
  settings: Settings | null
}) {
  const paths = transition.scan_paths ?? []
  return (
    <div className="inspector" aria-label="Transition inspector">
      <div className="inspector-head">
        <h2>Transition</h2>
      </div>
      <p className="transition-ends">
        <EndLink id={transition.source_id} canvas={canvas} select={select} /> →{' '}
        <EndLink id={transition.target_id} canvas={canvas} select={select} />
      </p>
      <div className="facts">
        {transition.direct ? (
          <span className="no-ts" title="Neither end is a transition-state node (D53)">
            Direct connection: no TS
          </span>
        ) : (
          <span>Through a transition-state node</span>
        )}
        {transition.cross_branch && <span>Between branches (interconversion)</span>}
        <span className={`status status-${transition.status}`}>{STATUS_LABEL[transition.status]}</span>
      </div>
      <section aria-label="Free species on this transition">
        <h3>Free species</h3>
        {transition.warnings.length > 0 && (
          <ul className="warnings" aria-label="Transition warnings">
            {transition.warnings.map((w, i) => (
              <li key={i}>
                <span className="badge warn">{w.code}</span> {w.message}
              </li>
            ))}
          </ul>
        )}
        {transition.species.length === 0 ? (
          <p className="muted">None join or leave here.</p>
        ) : (
          <ul className="plain species-list">
            {transition.species.map((s) => (
              <li key={s.species_id}>
                <span className={`chip species-${s.direction}`}>{speciesChip(s)}</span>{' '}
                <button className="link" onClick={() => select.onSelectNode(s.species_id)}>
                  {s.label}
                </button>
                {s.formula && <span className="muted small"> {s.formula}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
      {paths.length > 0 && (
        <section aria-label="Scan paths">
          <h3>Scan paths</h3>
          <p className="muted">GFN2-xTB paths run on this edge; each top is from its own first point, not this edge’s barrier.</p>
          <ul className="plain scan-path-rows">
            {paths.map((p) => (
              <li key={p.node_id}>
                <button className="link" onClick={() => select.onSelectNode(p.node_id)}>
                  {p.label || 'Untitled path'}
                </button>
                <span className={`chip scan-path-chip${pathMissed(p) ? ' missed' : ''}`}>{pathTop(p, settings)}</span>
                {pathMissed(p) && <span className="warn-text">did not reach end</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <NotesView notes={transition.notes} />
    </div>
  )
}

export function BranchPanel({ branch, canvas, select }: { branch: Branch; canvas: Canvas; select: Select }) {
  const byId = new Map(canvas.branches.map((b) => [b.id, b]))
  const members = canvas.nodes.filter((n) => n.branch_id === branch.id)
  const splitNode = canvas.nodes.find((n) => n.id === branch.split_node_id)
  const link = (id: string) => (
    <button key={id} className="link" onClick={() => select.onSelectBranch(id)}>
      {branchName(byId.get(id))}
    </button>
  )
  return (
    <div className="inspector" aria-label="Branch inspector">
      <div className="inspector-head">
        <span className="swatch large" style={{ background: branch.colour }} />
        <h2>{branchName(branch)}</h2>
        <span className={`status status-${branch.status}`}>{STATUS_LABEL[branch.status]}</span>
      </div>
      <section aria-label="Lineage" className="lineage">
        <h3>Lineage</h3>
        {branch.lineage_paths.map((path) => (
          <p key={path.join('-')} className="lineage-path">
            {path.map((id, i) => (
              <span key={id}>
                {i > 0 && ' → '}
                {id === branch.id ? <strong>{branchName(branch)}</strong> : link(id)}
              </span>
            ))}
          </p>
        ))}
        <p className="muted small">
          {branch.child_ids.length ? (
            <>
              Children:{' '}
              {branch.child_ids.map((id, i) => (
                <span key={id}>
                  {i > 0 && ', '}
                  {link(id)}
                </span>
              ))}
            </>
          ) : (
            'No child branches.'
          )}
        </p>
        {splitNode && (
          <p className="muted small">
            Split from{' '}
            <button className="link" onClick={() => select.onSelectNode(splitNode.id)}>
              {nodeName(splitNode)}
            </button>
          </p>
        )}
      </section>
      <NotesView notes={branch.notes} />
      <section aria-label="Branch nodes">
        <h3>Nodes ({members.length})</h3>
        <ul className="plain">
          {members.map((n) => (
            <li key={n.id}>
              <button className="link" onClick={() => select.onSelectNode(n.id)}>
                {nodeName(n)}
              </button>
              {n.step_id && <span className="muted small"> · {canvas.steps.find((s) => s.id === n.step_id)?.name}</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

/** With nothing selected: what this copy is, then the overview (FR-OV-01) without history. */
export function AboutPanel({ data, select }: { data: SnapshotData; select: Select }) {
  const exported = new Date(data.exported_at)
  return (
    <div className="inspector overview" aria-label="Overview">
      <div className="inspector-head">
        <h2>{data.investigation.name}</h2>
      </div>
      <p className="snapshot-about">
        A read-only copy exported from Chembook3D {data.app_version} on {exported.toLocaleString()}. Nothing here can be
        changed. Select a node, transition, group or branch to see its details, calculations and 3D structure.
      </p>
      <OverviewSections
        data={data.overview}
        canvas={data.canvas}
        onSelectNode={(id) =>
          data.canvas.groups.some((g) => g.id === id) ? select.onSelectGroup(id) : select.onSelectNode(id)
        }
        onSelectTransition={select.onSelectTransition}
        onSelectBranch={select.onSelectBranch}
      />
    </div>
  )
}
