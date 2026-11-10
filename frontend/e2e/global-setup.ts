import { execFileSync } from 'node:child_process'
import { join, resolve } from 'node:path'

/**
 * Builds the demo investigation (scripts/make_demo.py) once, as the template that each test which
 * opens it copies first (helpers.ts, demoFolder), and writes the synthetic Gaussian files
 * (tests/gaussian_text.py) that the tests import: a custom basis set, TS searches reading their
 * basis from a checkpoint and a TS with its IRC (D131).
 */
export default function globalSetup() {
  const root = resolve(import.meta.dirname, '..', '..')
  const run = (args: string[]) => execFileSync('uv', ['run', 'python', ...args], { cwd: root, stdio: 'inherit' })
  run(['scripts/make_demo.py', join(process.env.E2E_DIR!, 'demo-template')])
  run(['-m', 'tests.gaussian_text', join(process.env.E2E_DIR!, 'gaussian')])
  run(['-m', 'tests.gaussian_text', '--checkpoint', join(process.env.E2E_DIR!, 'checkpoint')])
  run(['-m', 'tests.gaussian_text', '--irc', join(process.env.E2E_DIR!, 'irc')])
}
