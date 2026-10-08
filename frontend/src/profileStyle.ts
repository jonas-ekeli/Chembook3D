// D106: the energy profile's style (the backend's `settings.PROFILE_PRESETS`), the colours and
// dashes it gives each pathway, and the PNG and SVG saved in it.

import type { ProfileStyle, Settings } from './api'
import { download } from './util'

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

/** The colour of each pathway in this style: its branch colour, or one of a fixed set. */
export function pathwayColours(colours: string[], style: ProfileStyle): string[] {
  const set = { 'colour-blind': COLOUR_BLIND, grey: GREYS, black: ['#000000'] }[style.colours as string]
  return set ? colours.map((_, i) => set[i % set.length]) : colours
}

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
  return new XMLSerializer().serializeToString(copy)
}

export function saveProfileSvg(svg: SVGSVGElement, name = 'energy-profile.svg') {
  const url = URL.createObjectURL(new Blob([figureText(svg)], { type: 'image/svg+xml' }))
  download(url, name)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
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
