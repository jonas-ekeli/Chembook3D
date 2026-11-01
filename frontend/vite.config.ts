import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During `npm run dev`, API calls go to the Python backend (`uv run chembook3d --no-browser`).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // ws: the Claude panel's terminal is a WebSocket (D92).
      '/api': { target: 'http://127.0.0.1:8765', ws: true },
    },
  },
  build: {
    // The licences of the bundled packages, served beside the app (THIRD-PARTY-NOTICES.md).
    license: { fileName: 'third-party-licenses.md' },
    // The chunks over 1 MB are Ketcher (7.6 MB with its parts), ELK and the app itself; the
    // libraries load on demand and the app is served locally, so their size costs little.
    chunkSizeWarningLimit: 8000,
    rolldownOptions: {
      onLog(level, log, handler) {
        // 3Dmol.js uses eval internally; that is its own code, not ours.
        if (log.code === 'EVAL' && log.id?.includes('3dmol')) return
        handler(level, log)
      },
    },
  },
})
