// D181: the energy profile as it is saved, apart from the chart on screen.

import { createElement } from 'react'
import { flushSync } from 'react-dom'
import { createRoot } from 'react-dom/client'
import { ProfileChart, type ChartProps } from './components/ProfileChart'

/** The figure rendered afresh in the profile style alone (D106): none of the screen's text
 * scale, zoom, hover or selection marks, whatever the chart on screen shows (D181). */
export function figureOf(props: Pick<ChartProps, 'data' | 'colours' | 'names' | 'settings' | 'marks' | 'shifts'>): SVGSVGElement {
  const holder = document.createElement('div')
  const root = createRoot(holder)
  flushSync(() => root.render(createElement(ProfileChart, props)))
  const svg = holder.querySelector('svg')!.cloneNode(true) as SVGSVGElement
  root.unmount()
  return svg
}
