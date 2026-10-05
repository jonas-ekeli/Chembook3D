// D91: the tab waits on the backend for changes made elsewhere (by Claude through
// `chembook3d mcp`, or in another tab) and for Claude's requests to delete something.

import { useEffect, useRef, useState } from 'react'
import { api, type ClaudeRequest } from './api'

const WAIT = 25 // seconds per long poll; the backend answers at once when something happens

/** Calls `onChanged` after a change made elsewhere in the open investigation (`folder`), and
 * returns Claude's requests waiting for an answer, oldest first. */
export function useLive(folder: string | null, onChanged: () => void): ClaudeRequest[] {
  const [requests, setRequests] = useState<ClaudeRequest[]>([])
  const changed = useRef(onChanged)
  useEffect(() => {
    changed.current = onChanged
  }, [onChanged])

  useEffect(() => {
    if (!folder) return
    const abort = new AbortController()
    let since: number | null = null
    let confirmVersion: number | null = null
    const run = async () => {
      while (!abort.signal.aborted) {
        try {
          const live = await api.live(since, confirmVersion, since === null ? 0 : WAIT, abort.signal)
          if (live.changed) changed.current()
          since = live.version
          confirmVersion = live.confirm_version
          setRequests(live.confirmations)
        } catch {
          if (abort.signal.aborted) return
          // The backend restarting or briefly busy: try again shortly.
          await new Promise((resolve) => setTimeout(resolve, 2000))
        }
      }
    }
    void run()
    return () => {
      abort.abort()
      setRequests([])
    }
  }, [folder])

  return requests
}
