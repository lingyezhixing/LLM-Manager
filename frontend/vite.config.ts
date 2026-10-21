import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

// Backend for the dev-server proxy. Resolution order:
//   1. frontend/.env.local 的 VITE_BACKEND(本机开发用,已 gitignore,重启不丢)
//   2. 进程环境变量 VITE_BACKEND(临时覆盖)
//   3. 默认 8080(cfg.program.port)
// 之所以落到文件:config 变更会让 Vite 重启 dev server,而 shell 里 export 的变量
// 不保证跨重启存活 —— 代理静默退回 8080 会让人对着错误的后端调试。
const DEFAULT_BACKEND = "http://127.0.0.1:8080";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, "");
  const BACKEND = env.VITE_BACKEND || process.env.VITE_BACKEND || DEFAULT_BACKEND;
  return {
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
  };
});


