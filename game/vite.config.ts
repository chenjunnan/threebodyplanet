import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  base: '/game/',
  build: {
    outDir: '../src/threebody/web/static/game',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        game: resolve(import.meta.dirname, 'index.html'),
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
});
