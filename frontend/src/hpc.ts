import type { RemoteServer } from './api'

// D127: the side panel's HPC tab (SidePanel.tsx) and the header button that opens it.

export const HPC = 'hpc'

/** D127: clusters offered ready-filled when adding one (Sigma2's, as of 2026-10). */
export const CLUSTER_PRESETS = [
  { name: 'Saga', host: 'saga.sigma2.no' },
  { name: 'Betzy', host: 'betzy.sigma2.no' },
  { name: 'Olivia', host: 'olivia.sigma2.no' },
]

/** D127: the HPC button's name: "HPC" until logged in, then the cluster's ("Saga +1" for two). */
export function hpcLabel(servers: RemoteServer[]): string {
  const connected = servers.filter((s) => s.connected)
  if (connected.length === 0) return 'HPC'
  return connected.length === 1 ? connected[0].name : `${connected[0].name} +${connected.length - 1}`
}
