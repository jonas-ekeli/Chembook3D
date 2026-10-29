import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { PATHWAY_PRESETS } from '../profileStyle'

/** D195: a pathway's swatch in the drawer; a click opens its colours: a set to pick from, any
 * colour through the browser's colour field, and back to the branch's (or the default) colour.
 * The colour chosen wins over the branch's and over the profile style's set of colours. */
export function PathwayColour({
  name,
  colour,
  chosen,
  fallback,
  onChange,
}: {
  /** The pathway's name, for the labels. */
  name: string
  /** The colour it is drawn in. */
  colour: string
  /** The colour the user chose, or null. */
  chosen: string | null
  /** What null means: "Branch colour" or "Default colour". */
  fallback: string
  onChange: (colour: string | null) => void
}) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)
  // Placed on the window, so the scrolling pathways list never cuts it off: under the swatch, or
  // above it where there is no room below.
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    if (!open) return setPlace(null)
    const at = button.current?.getBoundingClientRect()
    const height = pop.current?.offsetHeight ?? 0
    if (!at) return
    const below = at.bottom + 4
    setPlace({ left: at.left, top: below + height > window.innerHeight - 8 ? Math.max(8, at.top - 4 - height) : below })
  }, [open])

  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!wrap.current?.contains(event.target as globalThis.Node)) setOpen(false)
    }
    // Placed once, so it closes when the list scrolls or the window changes size.
    const shut = (event: Event) => {
      if (!(event.target instanceof globalThis.Node && pop.current?.contains(event.target))) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('scroll', shut, true)
    window.addEventListener('resize', shut)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('scroll', shut, true)
      window.removeEventListener('resize', shut)
    }
  }, [open])

  const close = () => {
    setOpen(false)
    button.current?.focus()
  }
  const pick = (next: string | null) => {
    onChange(next)
    close()
  }

  return (
    <span className="pathway-colour" ref={wrap}>
      <button
        ref={button}
        className="swatch-button"
        aria-label={`Colour of ${name}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="Change this pathway's colour"
        onClick={() => setOpen((o) => !o)}
      >
        <span className="swatch" style={{ background: colour }} />
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={`Colour of ${name}`}
          className="colour-pop"
          ref={pop}
          style={place ?? { visibility: 'hidden' }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation()
              close()
            }
          }}
        >
          <div className="colour-presets" role="radiogroup" aria-label="Colours">
            {PATHWAY_PRESETS.map((c, i) => (
              <button
                key={c}
                role="radio"
                aria-checked={chosen === c}
                aria-label={`Colour ${i + 1}`}
                title={c}
                className="colour-preset"
                style={{ background: c }}
                autoFocus={chosen ? chosen === c : i === 0}
                onClick={() => pick(c)}
              />
            ))}
          </div>
          <label className="colour-custom">
            <input
              type="color"
              aria-label="Any colour"
              value={chosen ?? colour}
              onChange={(event) => onChange(event.target.value)}
            />
            <span>Any colour…</span>
          </label>
          <button className="small" disabled={chosen === null} onClick={() => pick(null)}>
            {fallback}
          </button>
        </div>
      )}
    </span>
  )
}
