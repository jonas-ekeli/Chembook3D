// D106: the energy profile's style (the backend's `settings.PROFILE_PRESETS`), the colours and
// dashes it gives each pathway, and the PNG and SVG saved in it.

import type { Branch, Canvas, ProfileStyle, Settings } from './api'
import { nodeName } from './names'
import { download, downloadBlob } from './util'

/** D106: the "Screen" style, the look before profile styles; the backend's
 * `settings.PROFILE_PRESETS["screen"]`, for copies made before D106 and until settings load. */
export const SCREEN_STYLE: ProfileStyle = {
  width: 960,
  height: 360,
  font: 'system',
  font_size: 11,
  colours: 'branch',
  text: 'soft',
  level_width: 60,
  level_thickness: 3.5,
  connector: 'straight',
  dash: 'solid',
  connector_width: 1.4,
  value_position: 'above',
  name_position: 'below',
  brackets: 'none',
  decimals: 'unit',
  title: true,
  legend: 'top-right',
  grid: true,
  y_axis: true,
  step_names: true,
  edge_tags: true,
  background: 'white',
  png_scale: 2,
}

export const FONTS: Record<ProfileStyle['font'], { label: string; family: string }> = {
  system: { label: 'System sans-serif', family: 'system-ui, sans-serif' },
  arial: { label: 'Arial', family: 'Arial, Helvetica, sans-serif' },
  helvetica: { label: 'Helvetica', family: 'Helvetica, Arial, sans-serif' },
  times: {
    label: 'Times New Roman',
    family: '"Times New Roman", Times, serif',
  },
}

// Okabe and Ito's colour-blind-safe set, then greys dark to light.
const COLOUR_BLIND = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9', '#000000']
const GREYS = ['#000000', '#5c5c5c', '#8f8f8f', '#b3b3b3']
const DASHES = [undefined, '7 4', '1.5 4', '9 3 2 3']

export function styleOf(settings: Settings | null | undefined): ProfileStyle {
  return { ...SCREEN_STYLE, ...settings?.profile_style }
}

/** The colour of each pathway in this style: its branch colour, or one of a fixed set; D195: a
 * colour the user chose for a pathway (`chosen`) wins over both. */
export function pathwayColours(colours: string[], style: ProfileStyle, chosen?: (string | null | undefined)[]): string[] {
  const set = { 'colour-blind': COLOUR_BLIND, grey: GREYS, black: ['#000000'] }[style.colours as string]
  return colours.map((colour, i) => chosen?.[i] ?? (set ? set[i % set.length] : colour))
}

// D174: a pathway's colour is a series colour, kept in code (not a CSS token), since the saved
// figures keep the colours set here in either theme.
const NO_BRANCH = '#98a2b3'
const FALLBACK = ['#2459c6', '#c4320a', '#079455', '#6938ef', '#b54708']

/** The names the energy drawer shows for the nodes and groups of the canvas. */
export function pathwayLabels(canvas: Pick<Canvas, 'nodes' | 'groups'>): Map<string, string> {
  const found = new Map<string, string>()
  for (const n of canvas.nodes) found.set(n.id, nodeName(n))
  for (const g of canvas.groups) found.set(g.id, g.label || 'Group')
  return found
}

/** A pathway's legend entry in the energy drawer and in the read-only copy's drawer (A30): the
 * colour of the branch it was built on (`branchId`), else of the last branch it passes, else the
 * next fallback colour by its place `at`; its name is that branch's, or "first → last". */
export function pathwayLegend(
  canvas: Pick<Canvas, 'nodes' | 'groups'>,
  branches: Map<string, Branch>,
  labels: Map<string, string>,
  ids: string[],
  branchId: string | null | undefined,
  at: number,
): { colour: string; name: string; branched: boolean } {
  const branchOf = (id: string) =>
    canvas.nodes.find((n) => n.id === id)?.branch_id ?? canvas.groups.find((g) => g.id === id)?.outgoing_branch_id ?? null
  const onBranch = branchId ?? [...ids].reverse().map(branchOf).find((b) => b)
  const colour = onBranch ? (branches.get(onBranch)?.colour ?? NO_BRANCH) : FALLBACK[at % FALLBACK.length]
  const branch = branchId ? branches.get(branchId) : undefined
  const name = branch ? branch.name || 'Unnamed branch' : `${labels.get(ids[0])} → ${labels.get(ids.at(-1)!)}`
  return { colour, name, branched: Boolean(onBranch) }
}

/** D195: the colours offered first for a pathway: the colour-blind-safe set, then a few more. */
export const PATHWAY_PRESETS = [...COLOUR_BLIND, '#2459c6', '#c4320a', '#079455', '#6938ef', '#98a2b3']

export function dashOf(style: ProfileStyle, index: number): string | undefined {
  const w = style.connector_width
  const scaled = (dash: string | undefined) => dash?.replace(/[\d.]+/g, (n) => String(+n * Math.max(1, w / 1.4)))
  if (style.dash === 'dashed') return scaled(DASHES[1])
  if (style.dash === 'dotted') return scaled(DASHES[2])
  if (style.dash === 'by-pathway') return scaled(DASHES[index % DASHES.length])
  return undefined
}

/** The figure as SVG text, without the on-screen size that zoom gives it. */
function figureText(svg: SVGSVGElement): string {
  const copy = svg.cloneNode(true) as SVGSVGElement
  copy.removeAttribute('style')
  copy.removeAttribute('class')
  copy.querySelectorAll('[data-check]').forEach((mark) => mark.remove()) // D154: on screen only
  return new XMLSerializer().serializeToString(copy)
}

export function saveProfileSvg(svg: SVGSVGElement, name = 'energy-profile.svg') {
  downloadBlob(new Blob([figureText(svg)], { type: 'image/svg+xml' }), name)
}

/** The figure at the style's size times its PNG resolution, on white unless the background is
 * transparent (D106). */
export function saveProfilePng(
  svg: SVGSVGElement,
  style: ProfileStyle,
  onError: () => void,
  name = 'energy-profile.png',
) {
  const image = new Image()
  const scale = style.png_scale
  image.onload = () => {
    const canvasEl = document.createElement('canvas')
    canvasEl.width = Math.round(style.width * scale)
    canvasEl.height = Math.round(style.height * scale)
    const context = canvasEl.getContext('2d')!
    if (style.background === 'white') {
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, canvasEl.width, canvasEl.height)
    }
    context.drawImage(image, 0, 0, canvasEl.width, canvasEl.height)
    download(canvasEl.toDataURL('image/png'), name)
  }
  image.onerror = onError
  image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(figureText(svg))}`
}
