import { chmodSync, mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig } from '@playwright/test'

// UI checks against the real backend serving the built interface (run `npm run build` first).
// Everything the tests write goes to a fresh temporary folder, including app settings.
// realpath expands Windows short names (RUNNER~1), matching the paths the backend shows.
process.env.E2E_DIR ??= realpathSync.native(mkdtempSync(join(tmpdir(), 'chembook3d-e2e-')))
const port = 8766

// D92: the Claude panel runs tests/fake_claude.py instead of Claude Code, through a `claude`
// command as Windows (a .cmd file, as npm installs it) or a shell would find it.
function fakeClaude(): string {
  const root = resolve(import.meta.dirname, '..')
  const windows = process.platform === 'win32'
  const python = windows ? join(root, '.venv', 'Scripts', 'python.exe') : join(root, '.venv', 'bin', 'python')
  const script = join(root, 'tests', 'fake_claude.py')
  const folder = join(process.env.E2E_DIR!, 'bin')
  mkdirSync(folder, { recursive: true })
  const path = join(folder, windows ? 'claude.cmd' : 'claude')
  writeFileSync(path, windows ? `@"${python}" "${script}" %*\r\n` : `#!/bin/sh\nexec "${python}" "${script}" "$@"\n`)
  if (!windows) chmodSync(path, 0o755)
  return path
}

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1, // the backend has one open investigation at a time
  retries: 0,
  // The CI runners (Windows above all) are sometimes slow to create an investigation or parse a
  // large output; a lost wait there leaves a request running into the next test.
  expect: { timeout: process.env.CI ? 15_000 : 5_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    viewport: { width: 1600, height: 1000 },
    trace: 'retain-on-failure',
    permissions: ['clipboard-read', 'clipboard-write'],
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  webServer: {
    command: `uv run chembook3d --no-browser --port ${port}`,
    cwd: resolve(import.meta.dirname, '..'),
    url: `http://127.0.0.1:${port}/api/health`,
    reuseExistingServer: false,
    env: {
      CHEMBOOK3D_CONFIG_DIR: join(process.env.E2E_DIR, 'config'),
      CHEMBOOK3D_CLAUDE: fakeClaude(),
      // D93: a cloud job's launch first asks a question, as the folder trust question does.
      FAKE_CLAUDE_CLOUD: 'ask',
      FAKE_CLAUDE_LOG: join(process.env.E2E_DIR, 'fake-claude-cloud.log'),
    },
  },
})
