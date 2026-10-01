import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/** Puts the script and the styles into the page itself, so the read-only copy (D79) is one
 * file that opens from disk with no server. */
function singleFile(): Plugin {
  return {
    name: 'chembook3d-single-file',
    enforce: 'post',
    generateBundle(_, bundle) {
      const page = Object.values(bundle).find((file) => file.fileName.endsWith('.html'))
      if (!page || page.type !== 'asset') throw new Error('snapshot.html was not built')
      let html = String(page.source)
      for (const [name, file] of Object.entries(bundle)) {
        if (file.type === 'chunk') {
          // A module script may not contain "</script", which would end the element early.
          // With every import inlined, Vite leaves its preload placeholder for the dynamic
          // import of 3Dmol.js unfilled; there is nothing to preload, which is `void 0`.
          const code = file.code.replaceAll('</script', '<\\/script').replaceAll('__VITE_PRELOAD__', 'void 0')
          const tag = new RegExp(`<script type="module" crossorigin src="[^"]*${file.fileName}"></script>`)
          if (!tag.test(html)) throw new Error(`no script tag for ${file.fileName}`)
          html = html.replace(tag, () => `<script type="module">${code}</script>`)
          delete bundle[name]
        } else if (file.fileName.endsWith('.css')) {
          const tag = new RegExp(`<link rel="stylesheet" crossorigin href="[^"]*${file.fileName}">`)
          if (!tag.test(html)) throw new Error(`no link tag for ${file.fileName}`)
          html = html.replace(tag, () => `<style>${String(file.source)}</style>`)
          delete bundle[name]
        }
      }
      const left = Object.keys(bundle).filter((name) => bundle[name] !== page)
      if (left.length) throw new Error(`files left beside the page: ${left.join(', ')}`)
      page.source = html
    },
  }
}

// `npm run build` builds this after the app. The backend fills in the data when exporting
// (src/chembook3d/api/snapshot.py).
export default defineConfig({
  plugins: [react(), singleFile()],
  publicDir: false,
  build: {
    outDir: 'dist-snapshot',
    emptyOutDir: true,
    assetsInlineLimit: Number.MAX_SAFE_INTEGER,
    cssCodeSplit: false,
    chunkSizeWarningLimit: 5000,
    modulePreload: false,
    rolldownOptions: {
      input: 'snapshot.html',
      output: { codeSplitting: false },
      onLog(level, log, handler) {
        // 3Dmol.js uses eval internally; that is its own code, not ours.
        if (log.code === 'EVAL' && log.id?.includes('3dmol')) return
        handler(level, log)
      },
    },
  },
})
