import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const api = { target: "http://127.0.0.1:8000", changeOrigin: true, proxyTimeout: 600_000, timeout: 600_000 };

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // Stable vendors in their own chunks so an app release does not re-download them on slow intranets.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          if (/node_modules\/(react|react-dom|scheduler|react-router|react-router-dom)\//.test(id)) return "vendor-react";
          if (id.includes("echarts") || id.includes("zrender")) return "vendor-echarts";
          if (id.includes("@revolist")) return "vendor-grid";
          if (id.includes("konva")) return "vendor-konva";
          if (id.includes("@ant-design/pro-") ) return "vendor-pro";
          if (id.includes("antd") || id.includes("@ant-design") || id.includes("rc-")) return "vendor-antd";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      "/api/tasks": { ...api, proxyTimeout: 10_800_000, timeout: 10_800_000 },
      "/api": api,
      "/health": api,
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"], // e2e/ is Playwright (see e2e/README.md)
    testTimeout: 20000,
    css: false,
  },
});
