import type { Canvas, Group, Node } from './api'
import { MAX_OVERLAY } from './components/OverlayDialog'

/** An action of a panel's button row, which its record's right-click menu offers too (D184). */
export type PanelAction = { key: string; label: string; title: string; disabled: boolean; primary?: boolean }

/** A group's Overlay members and Compare sterics (FR-3D-04, D81). */
export function groupActions(group: Group, canvas: Canvas): PanelAction[] {
  const shapes = group.member_ids.filter((id) => canvas.nodes.some((n) => n.id === id && n.xyz)).length
  return [
    {
      key: 'overlay',
      label: 'Overlay members',
      disabled: shapes < 2 || shapes > MAX_OVERLAY,
      title: 'Superpose the members with coordinates, on the representative if there is one (FR-3D-04)',
    },
    {
      key: 'sterics',
      label: 'Compare sterics',
      disabled: shapes < 1,
      title: 'Buried volume and steric maps of the members (D81)',
    },
  ]
}

/** The selection's actions, in the panel's order: add to a group selected with the nodes (A20),
 * create a group (WF-07), overlay (FR-3D-04), match atoms (D113), scan path (D114), compare sterics
 * (D81), selectivity (D83) and conformer equilibrium (D171). D184: its right-click menu offers the
 * same. */
export function selectionActions(nodes: Node[], groups: Group[], canvas: Canvas): PanelAction[] {
  const free = nodes.filter((n) => !n.group_id)
  const target = groups.length === 1 ? groups[0] : null
  const joining = target ? nodes.filter((n) => n.group_id !== target.id) : []
  const scanEnds = scanPathEnds(nodes, groups, canvas)
  const actions: PanelAction[] = []
  if (groups.length > 0)
    actions.push({
      key: 'add-to-group',
      label: target ? `Add to “${target.label || 'Group'}”…` : 'Add to group…',
      disabled: !target || joining.length === 0,
      title: 'A20',
      primary: true,
    })
  actions.push(
    {
      key: 'create-group',
      label: 'Create group…',
      disabled: nodes.length < 2 || groups.length > 0 || free.length !== nodes.length,
      title: 'WF-07',
    },
    {
      key: 'overlay',
      label: 'Overlay in 3D',
      disabled: nodes.length < 2 || nodes.length > MAX_OVERLAY || nodes.some((n) => !n.xyz),
      title: `2 to ${MAX_OVERLAY} nodes with coordinates (FR-3D-04)`,
    },
    {
      key: 'match',
      label: 'Match atoms…',
      disabled: nodes.length !== 2 || groups.length > 0 || nodes.some((n) => !n.xyz),
      title: "Match the second structure's atoms to the first one's numbering (D113)",
    },
    {
      key: 'scan-path',
      label: 'Scan path…',
      disabled: !scanEnds,
      title: scanEnds
        ? 'A path of xTB structures from one to the other, designed and run by a Claude Code cloud session (D114)'
        : 'Select two nodes or groups joined by an edge',
    },
    {
      key: 'sterics',
      label: 'Compare sterics',
      disabled: nodes.filter((n) => n.xyz).length < 1,
      title: 'Buried volume and steric maps of the selected nodes (D81)',
    },
    {
      key: 'selectivity',
      label: 'Selectivity…',
      disabled: false,
      title: 'ΔΔG‡ and the predicted ratio, with each selected TS or group as one outcome (D83)',
    },
    {
      key: 'equilibrium',
      label: 'Conformer equilibrium…',
      disabled: false,
      title: 'Populations of the selected conformers and the rates of the TSs between them (D171)',
    },
  )
  return actions
}

/** D114: the two selected items (nodes or groups) of a scan path, start first, when an edge
 * joins them (also through a member's group, D108); the edge's direction gives the start. */
export function scanPathEnds(nodes: Node[], groups: Group[], canvas: Canvas): [string, string] | null {
  const items = [...nodes.map((n) => ({ id: n.id, ids: [n.id, n.group_id].filter(Boolean) as string[] })), ...groups.map((g) => ({ id: g.id, ids: [g.id] }))]
  if (items.length !== 2) return null
  const [a, b] = items
  const joins = (from: string[], to: string[]) => canvas.transitions.some((t) => from.includes(t.source_id) && to.includes(t.target_id))
  if (joins(a.ids, b.ids)) return [a.id, b.id]
  if (joins(b.ids, a.ids)) return [b.id, a.id]
  return null
}
