// D107: every tab keeps a long poll on the backend, so a server started by the launcher knows a
// tab is open and stops when the last one has closed; the tab says goodbye as it closes. The
// tab also learns when the server stops (Shut down here or in another tab) or has gone away.

import { useSyncExternalStore } from 'react'
import { api, CLIENT_ID } from './api'

export type Stopped = {
  /** shutdown: Shut down was pressed (here or in another tab); gone: the server stopped answering. */
  how: 'shutdown' | 'gone'
  /** How the push of a synced investigation went, when this tab knows. */
  sync: string | null
}

export type Lifecycle = {
  launched: boolean
  /** The launcher's notices: why the checkout was not updated, or the old interface is shown. */
  notices: string[]
  stopped: Stopped | null
}

const WAIT = 25 // seconds per long poll; the backend answers at once when it starts stopping
const RETRY = 2000 // ms between attempts when the backend does not answer
const GONE_AFTER = 3 // failed attempts in a row before the tab says the server is not running

let state: Lifecycle = { launched: false, notices: [], stopped: null }
const listeners = new Set<() => void>()

function update(next: Partial<Lifecycle>) {
  state = { ...state, ...next }
  listeners.forEach((listener) => listener())
}

const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function keepInTouch() {
  let failures = 0
  let wait = 0
  for (;;) {
    try {
      const presence = await api.presence(wait)
      failures = 0
      wait = WAIT
      update({ launched: presence.launched, notices: presence.notices })
      if (presence.stopping) {
        update({ stopped: { how: 'shutdown', sync: presence.sync } })
        return
      }
    } catch {
      if (state.stopped) return
      failures += 1
      if (failures >= GONE_AFTER) {
        update({ stopped: { how: 'gone', sync: null } })
        void comeBack()
        return
      }
      await pause(RETRY)
    }
  }
}

/** A server that went away may be started again: the tab then shows the app again. */
async function comeBack() {
  for (;;) {
    await pause(3000)
    if (state.stopped?.how !== 'gone') return
    try {
      await api.health()
      window.location.reload()
      return
    } catch {
      // still not running
    }
  }
}

let started = false

/** Starts the tab's presence once; called when the page loads. */
export function startPresence() {
  if (started) return
  started = true
  window.addEventListener('pagehide', () => {
    // A reload says goodbye too; the server waits a few seconds for the tab to come back.
    navigator.sendBeacon(`/api/presence/${encodeURIComponent(CLIENT_ID)}/closed`)
  })
  void keepInTouch()
}

/** Shut down in the app (FR-RUN-04): the backend closes the investigation and stops. */
export async function shutDown() {
  const result = await api.shutDown()
  update({ stopped: { how: 'shutdown', sync: result.sync } })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useLifecycle(): Lifecycle {
  return useSyncExternalStore(subscribe, () => state)
}
