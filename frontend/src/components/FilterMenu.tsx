import { useEffect, useRef, useState } from 'react'
import { STATUSES, type Canvas } from '../api'
import { NO_FILTERS, type Filters } from '../canvasView'
import { Icon } from '../icons'

/** FR-CAN-05: filters by branch, status and step. They hide items and never change data. */
export function FilterMenu({ canvas, filters, onChange }: { canvas: Canvas; filters: Filters; onChange: (f: Filters) => void }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  // D180: closes as the app's menus do, on Escape (back onto the button) or a click outside.
  useEffect(() => {
    if (!open) return
    const outside = (event: PointerEvent) => {
      if (!wrap.current?.contains(event.target as globalThis.Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  const count = filters.branches.length + filters.statuses.length + filters.steps.length
  const toggle = (key: keyof Filters, value: string) => {
    const list = filters[key]
    onChange({ ...filters, [key]: list.includes(value) ? list.filter((v) => v !== value) : [...list, value] })
  }
  const box = (key: keyof Filters, value: string, label: string, colour?: string) => (
    <label key={value} className="check">
      <input type="checkbox" checked={!filters[key].includes(value)} onChange={() => toggle(key, value)} />
      {colour && <span className="swatch" style={{ background: colour }} />}
      <span>{label}</span>
    </label>
  )
  return (
    <div className="menu" ref={wrap}>
      <button ref={button} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Icon name="filter" />
        <span className="tb-label">Filters</span>
        {count > 0 && <span className="filter-count">{` (${count} hidden)`}</span>}
        <Icon name="chevron" />
      </button>
      {open && (
        <div
          className="menu-list filters"
          role="dialog"
          aria-label="Filters"
          onKeyDown={(event) => {
            if (event.key !== 'Escape') return
            event.stopPropagation()
            setOpen(false)
            button.current?.focus()
          }}
        >
          <fieldset>
            <legend>Branch</legend>
            {canvas.branches.map((b) => box('branches', b.id, b.name || 'Unnamed branch', b.colour))}
            {box('branches', 'none', 'No branch', '#98a2b3')}
          </fieldset>
          <fieldset>
            <legend>Status</legend>
            {STATUSES.map((s) => box('statuses', s.value, s.label))}
          </fieldset>
          <fieldset>
            <legend>Step</legend>
            {canvas.steps.map((s) => box('steps', s.id, s.name || 'Unnamed step'))}
            {box('steps', 'none', 'No step')}
          </fieldset>
          <div className="buttons">
            <button className="small" onClick={() => onChange(NO_FILTERS)} disabled={!count}>
              Show all
            </button>
            <button className="small" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
