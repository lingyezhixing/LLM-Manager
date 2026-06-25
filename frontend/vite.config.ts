import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Backend port = cfg.program.port (default 8080). Adjust if your config.yaml differs.
const BACKEND = "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    proxy: {
      "/api": BACKEND,
      "/health": BACKEND,
      "/v1": BACKEND,
      "/openapi.json": BACKEND,
    },
  },
  build: { outDir: "dist" },
});
