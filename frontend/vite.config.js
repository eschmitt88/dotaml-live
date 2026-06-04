import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies API calls to the FastAPI backend (config/serving.yaml port).
const API = 'http://localhost:8090'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': API,
      '/meta': API,
      '/model': API,
      '/health': API,
    },
  },
  build: { outDir: 'dist' },
})
