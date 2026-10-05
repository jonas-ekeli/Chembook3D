import { useEffect, useState } from 'react'
import { api, type BasisVariant, type CustomBasisDetail, type CustomBasisSummary } from '../api'
import { download, fileName } from '../util'
import { Modal } from './Modal'

const ANGULAR = 'SPDFGHIK'

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** D94: the saved custom basis sets (FR-CALC-03), each with its shells, exponents,
 * coefficients and ECP, and as a Gaussian basis file to download or copy. */
export function BasisSetsDialog({
  initialName,
  onClose,
  onSelectNode,
}: {
  initialName?: string
  onClose: () => void
  onSelectNode?: (id: string) => void
}) {
  const [bases, setBases] = useState<CustomBasisSummary[] | null>(null)
  const [chosen, setChosen] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.customBases().then(
      (list) => {
        setBases(list)
        setChosen((list.find((b) => b.name === initialName) ?? list[0])?.id ?? null)
      },
      (err: unknown) => setError(errorText(err)),
    )
  }, [initialName])

  const goTo = onSelectNode
    ? (id: string) => {
        onSelectNode(id)
        onClose()
      }
    : undefined

  return (
    <Modal title="Custom basis sets" wide onClose={onClose} actions={<button onClick={onClose}>Close</button>}>
      {error && <p role="alert">{error}</p>}
      {bases && bases.length === 0 && (
        <p className="muted">
          No custom basis sets yet. The first import of a Gen or GenECP calculation asks for a name and saves the basis
          set here.
        </p>
      )}
      {bases && bases.length > 0 && (
        <div className="basis-sets">
          <ul className="basis-list" aria-label="Saved basis sets">
            {bases.map((b) => (
              <li key={b.id}>
                <button aria-pressed={chosen === b.id} onClick={() => setChosen(b.id)}>
                  <strong>{b.name}</strong>
                  <span className="muted small">{b.elements.join(' ')}</span>
                  <span className="muted small">
                    {b.calculation_count} calculation{b.calculation_count === 1 ? '' : 's'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {chosen && <BasisSetDetail key={chosen} id={chosen} onSelectNode={goTo} />}
        </div>
      )}
    </Modal>
  )
}

function BasisSetDetail({ id, onSelectNode }: { id: string; onSelectNode?: (id: string) => void }) {
  const [basis, setBasis] = useState<CustomBasisDetail | null>(null)
  const [included, setIncluded] = useState<Set<string>>(new Set())
  const [shown, setShown] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.customBasis(id).then(
      (b) => {
        setBasis(b)
        setIncluded(new Set(b.elements.map((e) => e.element)))
      },
      (err: unknown) => setError(errorText(err)),
    )
  }, [id])

  if (error) return <p role="alert">{error}</p>
  if (!basis) return <p className="muted">Loading…</p>

  const chosen = basis.elements.map((e) => e.element).filter((el) => included.has(el))
  const all = chosen.length === basis.elements.length
  const url = api.customBasisFileUrl(basis.id, all ? undefined : chosen)
  const withEcp = basis.elements.some((e) => included.has(e.element) && e.variants[0].ecp)
  const headers = [...new Set(basis.elements.flatMap((e) => e.variants.map((v) => v.header)).filter(Boolean))]
  const nodes = new Map(basis.calculations.map((c) => [c.node_id, c.node_label]))

  const copy = () => {
    setMessage(null)
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`The file could not be made (${response.status})`)
        return response.text()
      })
      .then((text) => navigator.clipboard.writeText(text))
      .then(
        () => setMessage('Copied. Paste it after the blank line that ends the molecule specification.'),
        (err: unknown) => setError(errorText(err)),
      )
  }

  return (
    <section className="basis-detail" aria-label={`Basis set ${basis.name}`}>
      <h3>{basis.name}</h3>
      <p className="muted small">
        Saved {new Date(basis.created_at).toLocaleString()} from the first file that used it
        {headers.length > 0 && <>, where Gaussian read it as {headers.join(', ')}</>}. Exponents and coefficients are
        exactly as Gaussian printed them.
      </p>
      {basis.description && <p>{basis.description}</p>}
      <table className="energy-table" aria-label="Elements">
        <thead>
          <tr>
            <th>In file</th>
            <th>Element</th>
            <th>Primitives/contracted</th>
            <th>ECP</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {basis.elements.map(({ element, variants }) => (
            <ElementRows
              key={element}
              element={element}
              variants={variants}
              included={included.has(element)}
              onInclude={(on) => {
                const next = new Set(included)
                if (on) next.add(element)
                else next.delete(element)
                setIncluded(next)
              }}
              shown={shown === element}
              onShow={() => setShown(shown === element ? null : element)}
            />
          ))}
        </tbody>
      </table>
      <div className="row">
        <button className="primary" disabled={chosen.length === 0} onClick={() => download(url, `${fileName(basis.name, 'basis')}.gbs`)}>
          Download Gaussian file (.gbs)
        </button>
        <button disabled={chosen.length === 0} onClick={copy}>
          Copy
        </button>
        <span className="muted small">
          {all ? 'All elements' : chosen.join(' ') || 'No elements'}; use with {withEcp ? 'GenECP' : 'Gen'}.
        </span>
      </div>
      {message && (
        <p className="muted small" role="status">
          {message}
        </p>
      )}
      <h4>Used by</h4>
      {basis.calculations.length === 0 ? (
        <p className="muted small">No calculation uses this name now.</p>
      ) : (
        <p className="small">
          {basis.calculations.length} calculation{basis.calculations.length === 1 ? '' : 's'} on{' '}
          {[...nodes].map(([nodeId, label], i) => (
            <span key={nodeId}>
              {i > 0 && ', '}
              {onSelectNode ? (
                <button className="link" onClick={() => onSelectNode(nodeId)}>
                  {label || 'Untitled node'}
                </button>
              ) : (
                label || 'Untitled node'
              )}
            </span>
          ))}
          {basis.calculations.some((c) => c.geometry_level_only) && (
            <span className="muted"> (some only as the geometry level of a single point)</span>
          )}
        </p>
      )}
    </section>
  )
}

function ElementRows({
  element,
  variants,
  included,
  onInclude,
  shown,
  onShow,
}: {
  element: string
  variants: BasisVariant[]
  included: boolean
  onInclude: (on: boolean) => void
  shown: boolean
  onShow: () => void
}) {
  return (
    <>
      {variants.map((v, i) => (
        <tr key={i} className={i > 0 ? 'muted' : undefined}>
          <td>
            {i === 0 && (
              <input
                type="checkbox"
                aria-label={`Include ${element}`}
                checked={included}
                onChange={(event) => onInclude(event.target.checked)}
              />
            )}
          </td>
          <td>
            {element}
            {variants.length > 1 && ` (${i + 1} of ${variants.length})`}
          </td>
          <td className="mono">{v.contraction || '—'}</td>
          <td>
            {v.ecp
              ? `${v.ecp.core_electrons ?? '?'} core electrons, up to ${ANGULAR[v.ecp.max_angular] ?? v.ecp.max_angular}`
              : '—'}
          </td>
          <td>
            {i === 0 && (
              <button className="small" aria-expanded={shown} onClick={onShow}>
                {shown ? 'Hide functions' : 'Show functions'}
              </button>
            )}
          </td>
        </tr>
      ))}
      {shown && (
        <tr>
          <td colSpan={5}>
            {variants.length > 1 && (
              <p className="muted small">
                Atoms of {element} had {variants.length} different definitions in the first file; the downloaded file
                has the first.
              </p>
            )}
            {variants.map((v, i) => (
              <pre key={i} className="route basis-text" aria-label={`${element} functions`}>
                {v.text.join('\n')}
              </pre>
            ))}
          </td>
        </tr>
      )}
    </>
  )
}
