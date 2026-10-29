import type { GroupPopulation } from './api'

// D197: a group's members' population % as their cards and the group's panel show it.

/** 62 % as "62 %", small ones in two figures, none below 0.01 %. */
export function percentText(percent: number): string {
  if (percent >= 9.95) return `${percent.toFixed(0)} %`
  if (percent >= 0.995) return `${percent.toFixed(1)} %`
  if (percent >= 0.01) return `${percent.toPrecision(2)} %`
  return '< 0.01 %'
}

/** What a group's head says: how many members the shares are of, or why there are none. */
export function populationSummary(found: GroupPopulation): { text: string; title: string } {
  const what = found.kind === 'transition_states' ? 'Shares of the flux' : 'Populations'
  const basis = `${what} from ${found.type} at ${found.temperature} K`
  if (found.status !== 'ok') return { text: '% n/a', title: [found.message ?? 'No shares', ...found.notes].join('\n') }
  const text = found.counted < found.total ? `% of ${found.counted}/${found.total}` : '%'
  const of =
    found.counted < found.total
      ? `, of the ${found.counted} of ${found.total} members counted`
      : `, of all ${found.total} members`
  return { text, title: [`${basis}${of}`, ...found.notes].join('\n') }
}

/** A member card's share, or a dash with why it is not counted. */
export function memberShare(found: GroupPopulation, memberId: string): { text: string; title: string; counted: boolean } {
  const own = found.members[memberId]
  if (own?.percent == null) return { text: '— %', title: own?.message ?? 'Not counted', counted: false }
  const what = found.kind === 'transition_states' ? 'share of the flux' : 'population'
  return {
    text: percentText(own.percent),
    title: `Boltzmann ${what} from ${found.type} at ${found.temperature} K within the group`,
    counted: true,
  }
}
