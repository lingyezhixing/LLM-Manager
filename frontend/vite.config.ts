import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Backend for the Vite dev server proxy. Default = cfg.program.port (8080);
// 同时跑多个实例(如本仓库 8081 + 部署实例 8080)时用 VITE_BACKEND 覆盖。
const BACKEND = process.env.VITE_BACKEND ?? "http://127.0.0.1:8080";

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
