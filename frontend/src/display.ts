import { createContext } from 'react'
import type { HydrogenMode } from './chem'

/** The hydrogens setting, shared by every 3D view and structure card. */
export const HydrogenDisplay = createContext<HydrogenMode>('all')

export type StericColours = 'blue' | 'green-yellow-red' | 'rainbow' | 'viridis' | 'grey'

// The map's colours, low (far below the centre) to high (crowding the centre). D84: chosen in the
// settings; blue is the default, a single hue that reads light to dark.
export const STERIC_PALETTES: Record<StericColours, { name: string; stops: string[] }> = {
  blue: {
    name: 'Blue',
    stops: ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281', '#0d366b'],
  },
  'green-yellow-red': {
    name: 'Green–yellow–red',
    stops: ['#1a9850', '#66bd63', '#a6d96a', '#d9ef8b', '#ffffbf', '#fee08b', '#fdae61', '#f46d43', '#d73027'],
  },
  rainbow: {
    name: 'Rainbow (blue to red)',
    stops: ['#30123b', '#4662d7', '#36aaf9', '#1ae4b6', '#72fe5e', '#c8ef34', '#faba39', '#f66b19', '#ca2a04', '#7a0403'],
  },
  viridis: {
    name: 'Viridis',
    stops: ['#440154', '#482878', '#3e4989', '#31688e', '#26828e', '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'],
  },
  grey: { name: 'Grey', stops: ['#f2f2f2', '#d0d0d0', '#a8a8a8', '#7f7f7f', '#575757', '#333333', '#1a1a1a'] },
}

/** D84: the colours of every steric map (an app setting), changed from the settings or beside a map. */
export const StericColourSetting = createContext<{
  colours: StericColours
  setColours: (colours: StericColours) => void
}>({ colours: 'blue', setColours: () => undefined })
