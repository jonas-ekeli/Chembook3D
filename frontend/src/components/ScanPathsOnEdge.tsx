import { useState } from 'react'
import { api, pathMissed, pathTop, type Canvas, type Node, type PathOnEdge, type Settings, type Transition } from '../api'
import { StepMovie, type Movie } from './StepMovie'
import { Viewer3D } from './Viewer3D'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const pathName = (p: { label: string }) => p.label || 'Untitled path'

/** D118: the session's verdict on the path's end, in words. */
function gateText(p: PathOnEdge): string {
  if (pathMissed(p)) return 'did not reach end'
  if (p.gate === 'passed' || p.reached_end) return 'reached end'
  return 'no quality check'
}

function rmsdText(p: PathOnEdge): string | null {
  if (p.active_rmsd !== null) return `end RMSD ${p.active_rmsd.toFixed(2)} Å (reacting atoms)`
  if (p.end_rmsd !== null) return `end RMSD ${p.end_rmsd.toFixed(2)} Å`
  return null
}

/**
 * D121: the edge panel's "Scan paths": one row per path run on the edge, and the chosen one's
 * energy chart and movie, whose "Use this structure" and point removal act on the path node
 * exactly as in its own panel (D112, D117). The tops are each path's own (GFN2-xTB, from its
 * first kept point) and never enter ΔX or profiles (D27, EN-3).
 */
export function ScanPathsSection({
  transition,
  canvas,
  settings,
  refreshKey,
  onChanged,
  onSelectNode,
}: {
  transition: Transition
  canvas: Canvas
  settings: Settings | null
  refreshKey: number
  onChanged: (notice?: string) => void
  onSelectNode: (id: string) => void
}) {
  const paths = transition.scan_paths ?? []
  const [chosenId, setChosenId] = useState<string | null>(paths.length === 1 ? paths[0].node_id : null)
  const [movie, setMovie] = useState<Movie | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (paths.length === 0) return null
  const chosen = paths.find((p) => p.node_id === chosenId) ?? null
  const node = chosen ? canvas.nodes.find((n) => n.id === chosen.node_id) : undefined
  const setOnEdgeOnly = (p: PathOnEdge, value: boolean) =>
    api.setPathEdge(p.node_id, transition.id, value).then(
      () => onChanged(value ? `“${pathName(p)}” is shown on this edge only.` : `“${pathName(p)}” is back on the canvas.`),
      (err: unknown) => setError(errorText(err)),
    )
  return (
    <section aria-label="Scan paths">
      <h3>Scan paths</h3>
      <p className="muted">
        GFN2-xTB paths run on this edge. Each top is measured from that path’s first kept point; it is not this edge’s
        barrier and never enters ΔX or profiles.
      </p>
      <ul className="plain scan-path-rows">
        {paths.map((p) => {
          const rmsd = rmsdText(p)
          const pathNode = canvas.nodes.find((n) => n.id === p.node_id)
          return (
            <li key={p.node_id} className={p.node_id === chosenId ? 'chosen' : undefined}>
              <button
                className="link"
                aria-pressed={p.node_id === chosenId}
                onClick={() => {
                  setMovie(null)
                  setChosenId(p.node_id === chosenId ? null : p.node_id)
                }}
              >
                {pathName(p)}
              </button>
              <span className={`chip scan-path-chip${pathMissed(p) ? ' missed' : ''}`}>{pathTop(p, settings)}</span>
              {rmsd && <span className="muted">{rmsd}</span>}
              <span className={pathMissed(p) ? 'warn-text' : 'muted'}>{gateText(p)}</span>
              <label className="inline" title="Take the path node’s box off the canvas; this edge’s chip stands for it">
                <input
                  type="checkbox"
                  checked={pathNode?.on_edge_only ?? p.on_edge}
                  onChange={(event) => setOnEdgeOnly(p, event.target.checked)}
                />{' '}
                On this edge only
              </label>
              {pathNode?.on_edge_only && !p.on_edge && (
                <span className="muted" title="It has a calculation that is not a scan path, or edges or a group of its own">
                  (its box is shown)
                </span>
              )}
              <button className="small" onClick={() => onSelectNode(p.node_id)}>
                Open path node
              </button>
            </li>
          )
        })}
      </ul>
      {error && <p role="alert">{error}</p>}
      {chosen && node && (
        <Viewer3D
          key={node.id}
          models={node.xyz ? [{ xyz: node.xyz }] : []}
          rotation={node.view_rotation}
          trajectory={movie}
          below={
            <StepMovie
              nodeId={node.id}
              calculationId={chosen.calculation_id}
              refreshKey={refreshKey}
              settings={settings}
              vibrating={false}
              onShow={setMovie}
              onStart={() => undefined}
              onUsed={(_, derived, structure) =>
                onChanged(
                  derived
                    ? `Structure ${structure} saved as a new node derived from “${pathName(node)}”.`
                    : `“${pathName(node)}” now shows structure ${structure}.`,
                )
              }
              onTrimmed={(_, notice) => onChanged(notice)}
            />
          }
        />
      )}
    </section>
  )
}

/**
 * D121: in a scan path node's panel, the edge it is shown on: choose one, unlink it, and show
 * it on that edge only (its box off the canvas). A path imported from a file starts unlinked.
 */
export function PathEdgeSection({
  node,
  canvas,
  onChanged,
  onSelectTransition,
}: {
  node: Node
  canvas: Canvas
  onChanged: (node: Node, notice?: string) => void
  onSelectTransition: (id: string) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const name = (id: string) => {
    const n = canvas.nodes.find((x) => x.id === id)
    if (n) return n.label || 'Untitled node'
    const g = canvas.groups.find((x) => x.id === id)
    return g ? `${g.label || 'Group'} (group)` : 'deleted'
  }
  const edgeName = (t: Transition) => `${name(t.source_id)} → ${name(t.target_id)}`
  const edge = canvas.transitions.find((t) => t.id === node.path_edge_id)
  const set = (edgeId: string | null, onEdgeOnly?: boolean, notice?: string) =>
    api.setPathEdge(node.id, edgeId, onEdgeOnly).then(
      (updated) => {
        setError(null)
        onChanged(updated, notice)
      },
      (err: unknown) => setError(errorText(err)),
    )
  // A path's own edges are not where it was run.
  const choices = canvas.transitions.filter((t) => t.source_id !== node.id && t.target_id !== node.id)
  return (
    <section aria-label="Scan path on an edge">
      <h3>Shown on an edge</h3>
      {edge ? (
        <div className="field-with-action">
          <span>
            Chip on{' '}
            <button className="link" onClick={() => onSelectTransition(edge.id)}>
              {edgeName(edge)}
            </button>
          </span>
          <label className="inline" title="Take this box off the canvas; the edge’s chip stands for it">
            <input
              type="checkbox"
              checked={node.on_edge_only}
              onChange={(event) =>
                set(
                  edge.id,
                  event.target.checked,
                  event.target.checked ? 'Shown on its edge only; select the edge’s chip to open it.' : 'Back on the canvas.',
                )
              }
            />{' '}
            On that edge only
          </label>
          <button className="small" onClick={() => set(null, undefined, 'Unlinked from its edge.')}>
            Unlink from edge
          </button>
        </div>
      ) : (
        <label className="field">
          <span>Show this path on an edge</span>
          <select
            aria-label="Show path on edge"
            value=""
            onChange={(event) => event.target.value && set(event.target.value, undefined, 'Shown as a chip on its edge.')}
          >
            <option value="">Choose the edge it was run on…</option>
            {choices.map((t) => (
              <option key={t.id} value={t.id}>
                {edgeName(t)}
              </option>
            ))}
          </select>
        </label>
      )}
      {node.on_edge_only && !node.on_edge && edge && (
        <p className="muted">Its box is shown: it has a calculation that is not a scan path, or edges or a group of its own.</p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  )
}
