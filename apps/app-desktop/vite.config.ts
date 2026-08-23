import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Pinned because `tauri.conf.json` points `devUrl` here: if the CLI's port
  // guess and Vite's ever drifted, `tauri dev` would open a dead URL.
  server: { port: 5173, strictPort: true },
});
