import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// v2 dev console: bind to loopback by default per UI plan §8 (local-only).
// For team access, tunnel via Tailscale/VPN/ssh -L — not by exposing 0.0.0.0.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
