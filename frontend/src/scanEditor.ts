import { createContext } from 'react'
import type { ScanEditorTarget } from './api'

/** D135: opens the scan editor with these files; given by App to whatever offers "Open in Scan
 * editor" (the step movie of an imported scan). Null where there is none. */
export const OpenScanEditor = createContext<((targets: ScanEditorTarget[]) => void) | null>(null)
