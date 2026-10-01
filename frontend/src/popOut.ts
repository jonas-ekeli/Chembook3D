// The pop-out 3D view's place, size and state (D82), remembered per browser.
import { useState } from 'react'

/** Where the floating window sits and how big it is, in CSS pixels of the browser tab. */
export type Box = { x: number; y: number; width: number; height: number }

const STORAGE = 'chembook3d.viewer3d.popout'
const MIN_WIDTH = 280
const MIN_HEIGHT = 240
// Part of the title bar that must stay inside the tab, so the window can always be dragged back.
const GRIP = 80
const TITLE_HEIGHT = 32

export function defaultBox(): Box {
  const width = Math.min(560, window.innerWidth - 32)
  const height = Math.min(520, window.innerHeight - 120)
  return { x: Math.max(16, window.innerWidth - width - 24), y: 96, width, height }
}

export function fit(box: Box): Box {
  const width = Math.min(Math.max(box.width, MIN_WIDTH), Math.max(MIN_WIDTH, window.innerWidth))
  const height = Math.min(Math.max(box.height, MIN_HEIGHT), Math.max(MIN_HEIGHT, window.innerHeight))
  const x = Math.min(Math.max(box.x, GRIP - width), window.innerWidth - GRIP)
  const y = Math.min(Math.max(box.y, 0), window.innerHeight - TITLE_HEIGHT)
  return { x, y, width, height }
}

type Stored = { popped: boolean; box: Box | null }

export function read(): Stored {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE) ?? 'null') as Partial<Stored> | null
    const box = stored?.box
    const valid = box && [box.x, box.y, box.width, box.height].every((n) => typeof n === 'number' && Number.isFinite(n))
    return { popped: stored?.popped === true, box: valid ? box : null }
  } catch {
    return { popped: false, box: null }
  }
}

export function write(stored: Stored) {
  try {
    localStorage.setItem(STORAGE, JSON.stringify(stored))
  } catch {
    // per-browser convenience only
  }
}

/** Whether the 3D view is popped out (D82). Remembered in the browser, so selecting another
 * node, or reloading, keeps the view where the user put it. */
export function usePopOut(): [boolean, (popped: boolean) => void] {
  const [popped, setPopped] = useState(() => read().popped)
  const change = (next: boolean) => {
    setPopped(next)
    write({ ...read(), popped: next })
  }
  return [popped, change]
}

