// The last step of `npm run build`: prints the size of the app's main chunk and of the read-only
// copy's page (snapshot.html, D79), and fails when either is over its budget. The libraries that
// load on demand (Ketcher, ELK, 3Dmol.js, xterm) are left out: they grow with their own updates.
import { readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Budgets in bytes, each about 15% above the size measured when it was set (November 2026:
 * main chunk 1,176,372, snapshot.html 1,493,777). To raise one on purpose, build, set it to
 * the new size plus 15%, update the measured size above, and say why in the pull request.
 */
const BUDGETS = {
  main: 1_353_000,
  snapshot: 1_718_000,
}

const frontend = join(import.meta.dirname, '..')

/** The entry chunk is the one script the built index.html loads. */
function mainChunk() {
  const html = readFileSync(join(frontend, 'dist', 'index.html'), 'utf8')
  const found = /<script type="module"[^>]*src="\/([^"]+\.js)"/.exec(html)
  if (!found) throw new Error('dist/index.html loads no module script')
  return join(frontend, 'dist', ...found[1].split('/'))
}

const files = [
  { name: 'main chunk', path: mainChunk(), budget: BUDGETS.main },
  { name: 'snapshot.html', path: join(frontend, 'dist-snapshot', 'snapshot.html'), budget: BUDGETS.snapshot },
]

const kB = (bytes) => `${(bytes / 1000).toFixed(1)} kB`
let over = false
for (const { name, path, budget } of files) {
  const size = statSync(path).size
  const share = Math.round((size / budget) * 100)
  console.log(`${name}: ${kB(size)} of ${kB(budget)} (${share}%)`)
  if (size > budget) {
    over = true
    console.error(`${name} is over its budget; see BUDGETS in frontend/scripts/bundle-budget.mjs`)
  }
}
if (over) process.exit(1)
