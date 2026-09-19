import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Vite の build 成果物は FastAPI (thor.api) から配信するため、
// リポジトリ側の thor/api/static へ出力する。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../thor/api/static",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      // 開発時は FastAPI (127.0.0.1:8000) にフォワード
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
