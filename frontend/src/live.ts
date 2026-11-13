// D91: the tab waits on the backend for changes made elsewhere (by Claude through
// `chembook3d mcp`, or in another tab), for Claude's requests to delete something, and (D226) for what Claude
// asks the page itself to do.

import { useEffect, useRef, useState } from 'react'
import { api, type ClaudeRequest, type PageCommand } from './api'

const WAIT = 25 // seconds per long poll; the backend answers at once when something happens

/** Reconcile the global notebook before reloading its records, even from the start page. */
export function useLive(
  folder: string | null | undefined,
  onChanged: (folder: string | null) => void | Promise<void>,
): { requests: ClaudeRequest[]; commands: PageCommand[] } {
  const [requests, setRequests] = useState<ClaudeRequest[]>([])
  const [commands, setCommands] = useState<PageCommand[]>([])
  const changed = useRef(onChanged)
  useEffect(() => {
    changed.current = onChanged
  }, [onChanged])

  useEffect(() => {
    if (folder === undefined) return
    const abort = new AbortController()
    let since: number | null = null
    let confirmVersion: number | null = null
    const run = async () => {
      while (!abort.signal.aborted) {
        try {
          const live = await api.live(since, confirmVersion, since === null ? 0 : WAIT, abort.signal)
          if (abort.signal.aborted) return
          const identityChanged = live.investigation !== folder
          if (identityChanged || live.changed) await changed.current(live.investigation)
          if (abort.signal.aborted) return
          since = live.version
          confirmVersion = live.confirm_version
          setRequests(identityChanged ? [] : live.confirmations)
          setCommands(identityChanged ? [] : (live.commands ?? []))
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
      setCommands([])
    }
  }, [folder])

  return { requests, commands }
}
