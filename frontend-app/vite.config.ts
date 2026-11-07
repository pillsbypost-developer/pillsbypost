import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    // Polyfill global for browser
    global: 'globalThis',
  },
  resolve: {
    alias: {
      // Polyfill process for browser
      process: 'process/browser',
      util: 'util',
    },
  },
})
