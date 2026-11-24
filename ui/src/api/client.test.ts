import { describe, expect, it } from "vitest";

import { isTokenRequested } from "./auth";
import { toApiError } from "./client";

const res = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("toApiError (plan P9.5)", () => {
  it("maps the refinement cap", async () => {
    const err = await toApiError(res(409, { detail: "ticket t1 already has 5 refinements" }));
    expect(err.code).toBe("refinement_cap_reached");
  });
  it("maps the weekly run lock", async () => {
    const err = await toApiError(res(409, { detail: { message: "weekly run already in progress", run_id: "wr_1" } }));
    expect(err.code).toBe("weekly_run_locked");
    expect(err.message).toContain("wr_1");
  });
  it("never echoes input on validation errors", async () => {
    const err = await toApiError(res(422, { detail: [{ loc: ["body", "text"], type: "missing", msg: "Invalid request" }] }));
    expect(err.message).toBe("Invalid text.");
  });
  it("asks for the reviewer token on 401", async () => {
    const err = await toApiError(res(401, { detail: "unauthorized" }));
    expect(err.message).toMatch(/token/);
    expect(isTokenRequested()).toBe(true);
  });
});
