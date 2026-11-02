import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { api, ApiError, captureInvestigation, investigationUrl, setInvestigationIdentity } from '../src/api.ts'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

test('notebook reads and mutations send the displayed Unicode identity, discovery/open do not', async () => {
  const calls = []
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options })
    return Response.json({})
  }
  setInvestigationIdentity('C:/β notebook')
  await api.canvas()
  await api.saveViewState({})
  await api.live(null, null, 0)
  await api.currentInvestigation()
  await api.openInvestigation('B')
  await api.remoteStatus()
  assert.equal(calls[0].options.headers['X-Chembook-Investigation'], 'C%3A%2F%CE%B2%20notebook')
  assert.equal(calls[1].options.headers['X-Chembook-Investigation'], calls[0].options.headers['X-Chembook-Investigation'])
  for (const call of calls.slice(2)) assert.equal(call.options.headers['X-Chembook-Investigation'], undefined)
})

test('a start-page mutation explicitly expects no open investigation', async () => {
  globalThis.fetch = async (_path, options) => {
    assert.equal(options.headers['X-Chembook-Investigation'], '')
    return Response.json({})
  }
  setInvestigationIdentity(null)
  await api.saveSettings({ energy_unit: 'kcal/mol' })
})

test('a delayed old-notebook response is rejected after switching, even after switching back', async () => {
  const response = deferred()
  globalThis.fetch = () => response.promise
  setInvestigationIdentity('A')
  const pending = api.canvas()
  setInvestigationIdentity('B')
  setInvestigationIdentity('A')
  response.resolve(Response.json({ nodes: ['old A'] }))
  await assert.rejects(pending, (error) => error instanceof ApiError && error.status === 409)
})

test('identity is rechecked after the JSON body finishes reading', async () => {
  const body = deferred()
  const reading = deferred()
  globalThis.fetch = async () => ({
    ok: true,
    status: 200,
    json: () => { reading.resolve(); return body.promise },
  })
  setInvestigationIdentity('A')
  const pending = api.canvas()
  await reading.promise
  setInvestigationIdentity('B')
  body.resolve({ nodes: ['A'] })
  await assert.rejects(pending, (error) => error instanceof ApiError && error.status === 409)
})

test('CSV exports carry identity and reject a body that finishes in another notebook', async () => {
  const body = deferred()
  const reading = deferred()
  globalThis.fetch = async (_path, options) => {
    assert.equal(options.headers['X-Chembook-Investigation'], 'A')
    return { ok: true, status: 200, headers: new Headers(), blob: () => { reading.resolve(); return body.promise } }
  }
  setInvestigationIdentity('A')
  const pending = api.energyTableCsv({ paths: [['node']], reference_id: 'node', level: 'level', type: 'E' })
  await reading.promise
  setInvestigationIdentity('B')
  body.resolve(new Blob(['old,A']))
  await assert.rejects(pending, (error) => error instanceof ApiError && error.status === 409)
})

test('close protects its original target and retains its sync outcome after live reports closed', async () => {
  const response = deferred()
  globalThis.fetch = (_path, options) => {
    assert.equal(options.headers['X-Chembook-Investigation'], 'A')
    return response.promise
  }
  setInvestigationIdentity('A')
  const pending = api.closeInvestigation()
  setInvestigationIdentity(null)
  response.resolve(Response.json({ state: 'diverged', message: 'Choose a copy' }))
  assert.deepEqual(await pending, { state: 'diverged', message: 'Choose a copy' })
})

test('targeted sync resolution works from the start page without an active-identity header', async () => {
  globalThis.fetch = async (path, options) => {
    assert.equal(path, '/api/sync/resolve')
    assert.equal(options.headers['X-Chembook-Investigation'], undefined)
    assert.equal(JSON.parse(options.body).folder, 'A')
    return Response.json({ state: 'up_to_date' })
  }
  setInvestigationIdentity(null)
  assert.equal((await api.resolveSync('A', 'this')).state, 'up_to_date')
})

test('browser downloads and note images retain the identity from the render that made their URL', () => {
  setInvestigationIdentity('C:/A%20β')
  const urls = [api.xyzDownloadUrl('shared'), api.sourceFileUrl('shared'), api.customBasisFileUrl('shared', ['C', 'H']),
    investigationUrl('/api/turnovers/shared/table.csv'), investigationUrl('/api/note-images/shared')]
  setInvestigationIdentity('B')
  for (const value of urls) {
    const url = new URL(value, 'http://127.0.0.1')
    assert.equal(url.searchParams.get('expected_investigation'), 'C:/A%20β')
  }
  assert.equal(new URL(urls[2], 'http://127.0.0.1').searchParams.get('elements'), 'C,H')
})

test('queued profile operations finish in the same notebook but never dispatch after a switch', async () => {
  setInvestigationIdentity('A')
  const run = captureInvestigation()
  let mutations = 0
  const operation = async () => { mutations += 1; return 'saved' }
  assert.equal(await run(operation), 'saved')
  // Hiding/reopening the drawer does not change the notebook generation.
  assert.equal(await run(operation), 'saved')
  setInvestigationIdentity('B')
  await assert.rejects(run(operation), (error) => error instanceof ApiError && error.status === 409)
  assert.equal(mutations, 2)
})
