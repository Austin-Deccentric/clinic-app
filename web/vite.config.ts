import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: forward API calls to the local FastAPI. The dev server serves its own index.html, so there's no sid
// cookie; the proxy adds the key server-side instead (run with API_KEY set). It never reaches the bundle.
const api = { target: 'http://localhost:8000', headers: { 'X-API-Key': process.env.API_KEY ?? '' } }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/slots': api, '/bookings': api } },
})
