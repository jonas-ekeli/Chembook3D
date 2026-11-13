// D226: what Claude asks the open page to do (Tidy, open groups, show a record, change the
// view). The command comes with the tab's live poll; one tab takes it, does it with the same
// code as its own buttons, and reports how it went.

import { useEffect, useRef } from 'react'
import { api, type PageCommand } from './api'
import { useStable } from './stable'
import { errorText } from './util'

export type PageResult = Record<string, unknown>

/** A tab in the background lets one the user is looking at take the command first. */
const HIDDEN_WAIT = 1500

export function usePageCommands(
  commands: PageCommand[],
  run: (action: string, params: Record<string, unknown>, id: string) => PageResult | Promise<PageResult>,
): void {
  const latest = useStable(run)
  const taken = useRef(new Set<string>())
  useEffect(() => {
    const take = async (command: PageCommand) => {
      if (document.visibilityState !== 'visible') await new Promise((resolve) => setTimeout(resolve, HIDDEN_WAIT))
      try {
        await api.claimPageCommand(command.id)
      } catch {
        return // another tab took it, or it is no longer wanted
      }
      let report: { result?: PageResult; error?: string }
      try {
        report = { result: await latest(command.action, command.params, command.id) }
      } catch (err) {
        report = { error: errorText(err) }
      }
      await api.reportPageCommand(command.id, report).catch(() => undefined)
    }
    for (const command of commands) {
      if (taken.current.has(command.id)) continue
      taken.current.add(command.id)
      void take(command)
    }
  }, [commands, latest])
}
