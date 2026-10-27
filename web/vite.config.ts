import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5230,
    strictPort: true,
    proxy: {
      // 后端本地服务，统一前缀 /api/v1
      '/api': {
        target: process.env.MARKET_API_PROXY || 'http://127.0.0.1:8020',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1600,
  },
});
