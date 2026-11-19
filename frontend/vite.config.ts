import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.PARALLEL_API_PROXY || "http://localhost:8201";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL('./src/nomad', import.meta.url).pathname,
    "next/navigation": new URL('./src/nomad/navigation.ts', import.meta.url).pathname }, dedupe: ['react','react-dom'] },
  server: {
    host: "0.0.0.0",
    proxy: {
      "/mi/api/logins": { target: process.env.MI_CONSOLE_PROXY || "http://localhost:8410", rewrite: (path) => path.replace(/^\/mi/, "") },
      // Dev only: point the Upstox lane page at a preview copy of the lane (e.g. a new lane.py on its own port).
      ...(process.env.UPSTOX_LIVE_PROXY ? { "/api/upstox-live": process.env.UPSTOX_LIVE_PROXY } : {}),
      "/api": gateway,
      "/health": gateway,
      "/parallel": gateway,
      "/ws": { target: gateway, ws: true },
    },
  },
});
