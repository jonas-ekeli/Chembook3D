import type { Canvas } from './api'

/** Display names for every record on the canvas, for history entries that hold ids. */
export function recordNames(canvas: Canvas): Map<string, string> {
  return new Map<string, string>([
    ...canvas.nodes.map((n) => [n.id, n.label || 'Untitled node'] as [string, string]),
    ...canvas.species.map((n) => [n.id, n.label || 'Untitled species'] as [string, string]),
    ...canvas.steps.map((s) => [s.id, s.name || 'Unnamed step'] as [string, string]),
    ...canvas.branches.map((b) => [b.id, b.name || 'Unnamed branch'] as [string, string]),
    ...canvas.groups.map((g) => [g.id, g.label || 'Group'] as [string, string]),
  ])
}
