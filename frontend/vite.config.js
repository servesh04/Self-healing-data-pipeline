import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Tailwind v4 is configured in CSS (see src/index.css), not a JS config file.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
