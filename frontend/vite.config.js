import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev proxy: in production the ALB handles all routing.
// /api/*  → local FastAPI backend (port 8080)
// /rest/* → PostgREST on the ALB (direct DB reads)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
      '/rest': {
        target: 'https://payinvestigator-1594041664.us-west-2.elb.amazonaws.com',
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
