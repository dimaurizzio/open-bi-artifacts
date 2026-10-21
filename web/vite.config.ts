import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// El build lo sirve FastAPI en /static/app. En desarrollo (npm run dev) Vite hace
// de proxy al backend para la API, el visor y las páginas de artifact.
const backend = 'http://localhost:8765';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  base: '/static/app/',
  build: { outDir: '../server/static/app', emptyOutDir: true },
  server: {
    port: 5173,
    // /login y /auth también: el login vive en el backend (la cookie sirve en los dos puertos).
    proxy: { '/api': backend, '/viewer': backend, '/static/vendor': backend, '/login': backend, '/auth': backend },
  },
});
