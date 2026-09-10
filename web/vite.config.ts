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
    rollupOptions: {
      // The floor harness is a second entry: it renders the 3D scene alone so it can be
      // inspected (and verified) without the rest of the page.
      input: { index: 'index.html', harness: 'floor-check.html' },
    },
  },
})
