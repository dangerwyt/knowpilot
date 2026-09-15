
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, '.', './src/')
    }
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        ws: true,
        changeOrigin: true,
        // rewrite: (path) => {
        //   console.log(path, '-----------', path.replace(/^\/api\/v1/, '/api/v1'))
        //   return path.replace(/^\/api\/v1/, '/api/v1')
        // },
      },
    },
  },
})
