import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const gateway = process.env.PARALLEL_API_PROXY || "http://localhost:8201";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    proxy: {
      "/api": gateway,
      "/health": gateway,
      "/parallel": gateway,
      "/ws": { target: gateway, ws: true },
    },
  },
});
