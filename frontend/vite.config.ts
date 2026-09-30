import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During `npm run dev`, API calls go to the Python backend (`uv run chembook3d --no-browser`).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    // 3Dmol.js is one large library, loaded on demand; the app is served locally.
    chunkSizeWarningLimit: 1000,
    rolldownOptions: {
      onLog(level, log, handler) {
        // 3Dmol.js uses eval internally; that is its own code, not ours.
        if (log.code === 'EVAL' && log.id?.includes('3dmol')) return
        handler(level, log)
      },
    },
  },
})
