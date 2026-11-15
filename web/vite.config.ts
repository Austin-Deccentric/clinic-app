import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: forward API calls to the local FastAPI, so the page stays same-origin as in production.
const api = { target: 'http://localhost:8000' }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/slots': api, '/bookings': api } },
})
