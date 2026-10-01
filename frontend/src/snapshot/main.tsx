import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '../index.css'
import { readSnapshot } from './data'
import { SnapshotApp } from './SnapshotApp'

// The read-only copy (D79): one HTML file with the viewer and the investigation's data in it.
const root = createRoot(document.getElementById('root')!)
readSnapshot().then(
  (data) =>
    root.render(
      <StrictMode>
        <SnapshotApp data={data} />
      </StrictMode>,
    ),
  (err: unknown) =>
    root.render(
      <main className="start">
        <h1>Chembook3D</h1>
        <p role="alert">
          {err instanceof Error ? err.message : String(err)} It needs a current browser (Chrome, Edge, Firefox or Safari).
        </p>
      </main>,
    ),
)
