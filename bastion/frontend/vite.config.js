import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    proxy: {
      '/api': { target: 'http://localhost:8000', ws: false },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
