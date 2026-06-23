import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',

  plugins: [react()],
  
  server: {
    port: process.env.VITE_PORT ? parseInt(process.env.VITE_PORT) : 10000,
    host: process.env.VITE_HOST || '127.0.0.1',
    proxy: {
      '/api': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('API proxy error:', err);
          });
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('API Request:', req.method, req.url, '->', proxyReq.path);
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            console.log('API Response:', proxyRes.statusCode, req.url);
          });
        },
      },
      '/v1/models': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('Models API proxy error:', err);
          });
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('Models API Request:', req.method, req.url, '->', proxyReq.path);
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            console.log('Models API Response:', proxyRes.statusCode, req.url);
          });
        },
      }
    }
  }
})