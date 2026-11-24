import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

// End-to-end runs against the real FastAPI app with the cues_only model backend, a
// throwaway SQLite file and the local MCP backend, so no network or quota is used.
// FastAPI serves the built UI (ui/dist) at `/`, exactly as in production (plan P9.12).
const PORT = Number(process.env.E2E_PORT ?? 8799);
const OUT = path.resolve(import.meta.dirname, "test-results", "e2e-state");
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 30_000,
  expect: { timeout: 7_000 },
  use: {
    baseURL: external ?? `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 1000 } } }],
  webServer: external
    ? undefined
    : {
        // Build first so the server mounts the current UI; start from a clean state dir.
        command: `npm run build && rm -rf "${OUT}" && mkdir -p "${OUT}" && cd .. && uv run uvicorn app.main:app --host 127.0.0.1 --port ${PORT}`,
        url: `http://127.0.0.1:${PORT}/api/health`,
        reuseExistingServer: false,
        timeout: 180_000,
        env: {
          APP_ENV: "test",
          LLM_PROVIDER: "cues_only",
          MCP_BACKEND: "local",
          // Present but empty, so a developer's .env can't require a token here.
          ADMIN_TOKEN: "",
          EVAL_FAULTS: "",
          DATABASE_URL: `sqlite:///${OUT}/e2e.sqlite3`,
          LOCAL_DOC_PATH: `${OUT}/doc.md`,
          LOCAL_EMAIL_PATH: `${OUT}/email.md`,
          LOG_LEVEL: "WARNING",
        },
      },
});
