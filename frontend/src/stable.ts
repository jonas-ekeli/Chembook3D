import { useCallback, useLayoutEffect, useRef } from 'react'

/**
 * A handler with one identity for the component's life that calls the latest `fn`. For what is
 * handed to a memoised component or to React Flow, which compare their props by identity: a new
 * function on every render would draw them again (React Flow also copies some into its store).
 * Only for handlers called after rendering (events, effects), never during it.
 */
export function useStable<A extends unknown[], R>(fn: (...args: A) => R): (...args: A) => R {
  const latest = useRef(fn)
  useLayoutEffect(() => {
    latest.current = fn
  })
  return useCallback((...args: A) => latest.current(...args), [])
}

/** `useStable` for a handler that may be missing: undefined while it is, so a child can tell. */
export function useStableOptional<A extends unknown[], R>(
  fn: ((...args: A) => R) | undefined,
): ((...args: A) => R) | undefined {
  const stable = useStable((...args: A) => fn!(...args))
  return fn ? stable : undefined
}
