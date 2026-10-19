import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import '../ketcherGlobals'
import { Editor } from 'ketcher-react'
import type { Ketcher } from 'ketcher-core'
import { StandaloneStructServiceProvider } from 'ketcher-standalone/dist/binaryWasm'
import 'ketcher-react/dist/index.css'

declare global {
  interface Window {
    ketcher?: Ketcher
  }
}

// Indigo runs in the browser as WebAssembly: nothing leaves this computer (D142a).
const service = new StandaloneStructServiceProvider()

// Ketcher's own 3D view, reactions, shapes and text: nothing here turns them into a structure.
const hidden = { hidden: true }
const BUTTONS = {
  miew: hidden,
  arrows: hidden,
  'reaction-plus': hidden,
  'reaction-mapping-tools': hidden,
  shape: hidden,
  text: hidden,
  'create-monomer': hidden,
}

/** What the drawer hands back: the drawing as a MOL file for the 3D conversion, and in Ketcher's
 * own format so the next Draw… continues from it. */
export type Drawing = { molfile: string; ket: string }

/** D142: Ketcher, the 2D drawing editor, in a panel over the Edit structure window. It is loaded
 * only when Draw… is first opened (this module is imported lazily). */
export default function KetcherDrawer({
  start,
  help,
  busy,
  error,
  onConvert,
  onClose,
}: {
  start: string | null
  help: string
  busy: boolean
  error: string | null
  onConvert: (drawing: Drawing) => void
  onClose: (ket: string | null) => void
}) {
  const ketcher = useRef<Ketcher | null>(null)
  const [ready, setReady] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(
    () => () => {
      if (window.ketcher === ketcher.current) delete window.ketcher
    },
    [],
  )

  const current = async (): Promise<Drawing | null> => {
    const k = ketcher.current
    if (!k) return null
    return { molfile: await k.getMolfile(), ket: await k.getKet() }
  }

  const convert = async () => {
    setProblem(null)
    try {
      const drawing = await current()
      if (drawing) onConvert(drawing)
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err))
    }
  }

  const close = async () => {
    let ket: string | null = null
    try {
      ket = (await current())?.ket ?? null
    } catch {
      // A drawing Ketcher cannot write is not kept.
    }
    onClose(ket)
  }

  return createPortal(
    <div className="ketcher-drawer" role="dialog" aria-modal="true" aria-label="Draw">
      <div className="ketcher-drawer-head">
        <h2>Draw</h2>
        <span className="muted small">
          {help} Open a ChemDraw .cdxml or MOL file, or paste a SMILES, with Ketcher&apos;s Open button.
        </span>
      </div>
      <div className="ketcher-drawer-editor">
        <Editor
          staticResourcesUrl=""
          structServiceProvider={service}
          errorHandler={(message: string) => setProblem(message)}
          disableMacromoleculesEditor
          buttons={BUTTONS}
          onInit={(k: Ketcher) => {
            ketcher.current = k
            window.ketcher = k
            const done = () => setReady(true)
            if (start) k.setMolecule(start).then(done, done)
            else done()
          }}
        />
      </div>
      <div className="ketcher-drawer-foot">
        {(problem || error) && (
          <span className="small warning" role="alert">
            {problem || error}
          </span>
        )}
        <span className="spacer" />
        <button onClick={() => void close()}>Close</button>
        <button className="primary" disabled={!ready || busy} onClick={() => void convert()}>
          Convert to 3D
        </button>
      </div>
    </div>,
    document.body,
  )
}
