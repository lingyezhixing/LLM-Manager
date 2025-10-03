import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: process.env.VITE_PORT ? parseInt(process.env.VITE_PORT) : 10000,
    host: process.env.VITE_HOST || '127.0.0.1',
    proxy: {
      '^/api-root$': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api-root/, ''),
        configure: (proxy, _options) => {
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('API-ROOT Proxy Request:', req.method, req.url, '->', proxyReq.path);
          });
        }
      },
      '/api': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
        // 不移除 /api 前缀，因为后端需要这个前缀
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('Sending Request to the Target:', req.method, req.url);
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            console.log('Received Response from the Target:', proxyRes.statusCode, req.url);
          });
        },
      },
      '/health': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
      },
      '/v1/models': {
        target: `http://${process.env.VITE_API_HOST || '127.0.0.1'}:${process.env.VITE_API_PORT || 8080}`,
        changeOrigin: true,
        configure: (proxy, _options) => {
          proxy.on('error', (err, _req, _res) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req, _res) => {
            console.log('Sending Models API Request to the Target:', req.method, req.url);
          });
          proxy.on('proxyRes', (proxyRes, req, _res) => {
            console.log('Received Models API Response from the Target:', proxyRes.statusCode, req.url);
          });
        },
      }
    }
  }
})