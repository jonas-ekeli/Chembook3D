import { createContext } from 'react'
import type { HydrogenMode } from './chem'

/** The hydrogens setting, shared by every 3D view and structure card. */
export const HydrogenDisplay = createContext<HydrogenMode>('all')
