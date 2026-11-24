import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev and `vite preview` proxy /api to FastAPI, so the browser sees one origin.
// The production build is served by FastAPI itself at `/` (plan P9.12).
const apiTarget = process.env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8000";
const proxy = { "/api": { target: apiTarget, changeOrigin: true } };

// The MSW worker lives in mock-public/ so it never ships in the production build.
const mock = process.env.VITE_MOCK_API === "1";

export default defineConfig({
  plugins: [react()],
  publicDir: mock ? "mock-public" : "public",
  server: { port: 5173, proxy },
  preview: { port: 4173, proxy },
  build: {
    sourcemap: true,
    // Route chunks are split by React.lazy; keep vendor code in one cacheable chunk.
    rollupOptions: {
      output: {
        manualChunks: (id) => (id.includes("node_modules") ? "vendor" : undefined),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
  },
});
