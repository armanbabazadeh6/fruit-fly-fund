import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base so the same build works from a subpath (GitHub Pages) and from the
// local `flyvsly serve` root.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:7777', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
})
