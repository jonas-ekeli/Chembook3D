import { mkdtempSync, realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { defineConfig } from '@playwright/test'

// UI checks against the real backend serving the built interface (run `npm run build` first).
// Everything the tests write goes to a fresh temporary folder, including app settings.
// realpath expands Windows short names (RUNNER~1), matching the paths the backend shows.
process.env.E2E_DIR ??= realpathSync.native(mkdtempSync(join(tmpdir(), 'chembook3d-e2e-')))
const port = 8766

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
    env: { CHEMBOOK3D_CONFIG_DIR: join(process.env.E2E_DIR, 'config') },
  },
})
