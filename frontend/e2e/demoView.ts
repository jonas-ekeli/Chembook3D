import type { APIRequestContext } from '@playwright/test'

/**
 * D105: an investigation remembers its energy view, filters, expanded groups and drawer, so the
 * shared demo would open on whatever an earlier test left. This puts the view back to the
 * defaults before a test opens it; the investigation is left closed, as the tests expect.
 */
export async function forgetView(request: APIRequestContext, folder: string) {
  const opened = await request.post('/api/investigations/open', { data: { folder } })
  if (opened.ok()) await request.put('/api/view-state', { data: {} })
  await request.post('/api/investigations/close')
}
