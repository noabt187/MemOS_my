import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";


const rootDir = path.dirname(fileURLToPath(import.meta.url));


export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const applicationBackend = env.MEMOS_APP_API_URL || "http://127.0.0.1:8011";
  const apiProxy = {
    "/api/v1": {
      target: applicationBackend,
      changeOrigin: true,
    },
  };
  return {
    plugins: [react()],
    resolve: { alias: { "@": rootDir } },
    server: {
      proxy: apiProxy,
    },
    preview: {
      proxy: apiProxy,
    },
  };
});
