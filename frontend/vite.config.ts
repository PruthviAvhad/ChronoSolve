/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // The FastAPI backend. Keeping the UI on a same-origin /api path means the
    // demo works identically in dev and behind a single deployed origin.
    // Set VITE_API_TARGET when port 8000 is already taken by something else.
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
