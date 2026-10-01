import { useEffect, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { defaultBox, fit, read, write, type Box } from '../popOut'

/** The small button in the corner of the 3D view that pops it out or puts it back. */
export function PopOutButton({ popped, onClick }: { popped: boolean; onClick: () => void }) {
  const label = popped ? 'Put the 3D view back' : 'Pop out the 3D view'
  return (
    <button className="popout-toggle" onClick={onClick} aria-label={label} title={label}>
      {popped ? '⇲' : '⧉'}
    </button>
  )
}

/** Shows `children` in place or, when `popped`, in a floating window inside the browser tab
 * that can be dragged by its title bar and resized from its lower right corner (D82).
 *
 * The children are rendered once, into an element that is moved between the two places, so
 * the 3D viewer is not rebuilt: its turning, measurement and vibration carry over. */
export function PopOut({
  popped,
  title,
  onDock,
  children,
}: {
  popped: boolean
  title: string
  onDock: () => void
  children: ReactNode
}) {
  const content = useMemo(() => {
    const element = document.createElement('div')
    element.className = 'popout-content'
    return element
  }, [])
  const docked = useRef<HTMLDivElement>(null)
  const floating = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    const target = popped ? floating.current : docked.current
    target?.appendChild(content)
  }, [popped, content])
  useEffect(() => () => content.remove(), [content])

  return (
    <>
      <div ref={docked} className="popout-dock" hidden={popped} />
      {popped && (
        <div className="popout-placeholder muted">
          <span>The 3D view is in its own window.</span>
          <button className="small" onClick={onDock}>
            Put it back
          </button>
        </div>
      )}
      {popped && <FloatingWindow title={title} bodyRef={floating} onDock={onDock} />}
      {createPortal(children, content)}
    </>
  )
}

function FloatingWindow({
  title,
  bodyRef,
  onDock,
}: {
  title: string
  bodyRef: RefObject<HTMLDivElement | null>
  onDock: () => void
}) {
  const [box, setBox] = useState<Box>(() => fit(read().box ?? defaultBox()))

  // Keep the window reachable when the browser tab gets smaller.
  useEffect(() => {
    const onResize = () => setBox((current) => fit(current))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  const track = (event: ReactPointerEvent<HTMLElement>, move: (start: Box, dx: number, dy: number) => Box) => {
    if (event.button !== 0) return
    event.preventDefault()
    const handle = event.currentTarget
    const start = box
    let last = box
    const { clientX, clientY, pointerId } = event
    handle.setPointerCapture(pointerId)
    const onMove = (e: PointerEvent) => {
      last = fit(move(start, e.clientX - clientX, e.clientY - clientY))
      setBox(last)
    }
    const onUp = () => {
      handle.removeEventListener('pointermove', onMove)
      handle.removeEventListener('pointerup', onUp)
      handle.removeEventListener('pointercancel', onUp)
      write({ ...read(), box: last })
    }
    handle.addEventListener('pointermove', onMove)
    handle.addEventListener('pointerup', onUp)
    handle.addEventListener('pointercancel', onUp)
  }

  const startDrag = (event: ReactPointerEvent<HTMLElement>) => {
    if ((event.target as HTMLElement).closest('button')) return
    track(event, (start, dx, dy) => ({ ...start, x: start.x + dx, y: start.y + dy }))
  }
  const startResize = (event: ReactPointerEvent<HTMLElement>) =>
    track(event, (start, dx, dy) => ({ ...start, width: start.width + dx, height: start.height + dy }))

  return createPortal(
    <div
      className="popout-window"
      role="dialog"
      aria-label="3D view window"
      style={{ left: box.x, top: box.y, width: box.width, height: box.height }}
    >
      <div className="popout-title" onPointerDown={startDrag} title="Drag to move">
        <span className="popout-name">{title}</span>
        <button className="small" onClick={onDock} title="Put the 3D view back in the side panel">
          Put back
        </button>
      </div>
      <div ref={bodyRef} className="popout-body" />
      <div className="popout-resize" role="separator" aria-label="Resize" title="Drag to resize" onPointerDown={startResize} />
    </div>,
    document.body,
  )
}
