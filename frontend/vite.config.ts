import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard is served either by FastAPI (built assets) or by this dev
// server. In dev, /api and /api/ws are proxied so the browser only ever talks
// to one origin (no localhost URLs baked into client code).
const target = process.env.VITE_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: false,
    // Allow the sandbox preview host and any forwarded hostname.
    allowedHosts: true,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
