import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";

import { resetMockDb } from "../mocks/handlers";
import { server } from "../mocks/server";

beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetMockDb();
  try {
    sessionStorage.clear();
    localStorage.clear();
  } catch {
    // jsdom storage always exists; keep the guard for parity with the app.
  }
});
afterAll(() => server.close());
