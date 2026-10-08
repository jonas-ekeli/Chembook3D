import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'

/**
 * Builds the demo investigation (scripts/make_demo.py) that the tests open, and writes the
 * synthetic Gaussian files with a custom basis set (tests/gaussian_text.py) that they import.
 */
export default function globalSetup() {
  const root = resolve(import.meta.dirname, '..', '..')
  const run = (args: string[]) => execFileSync('uv', ['run', 'python', ...args], { cwd: root, stdio: 'inherit' })
  run(['scripts/make_demo.py', join(process.env.E2E_DIR!, 'demo')])
  run(['-m', 'tests.gaussian_text', join(process.env.E2E_DIR!, 'gaussian')])
  run(['-m', 'tests.gaussian_text', '--checkpoint', join(process.env.E2E_DIR!, 'checkpoint')])
}
