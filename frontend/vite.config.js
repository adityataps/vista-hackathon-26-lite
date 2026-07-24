import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Local dev proxies /api/* to the FastAPI app on :8080.
// Production builds call the backend Lambda Function URL via VITE_API_BASE_URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
});
