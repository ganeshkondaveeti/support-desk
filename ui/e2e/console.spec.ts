import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { createBlockedTicket, createOpenTicket, ROUTES, settle, waitForSeed } from "./helpers";

test.beforeAll(async ({ request }) => {
  await waitForSeed(request);
});

test("1. the disclaimer is visible on all five routes", async ({ page }) => {
  for (const route of ROUTES) {
    await page.goto(route);
    await expect(page.getByTestId("disclaimer").first()).toHaveText("Facts-only. No investment advice.");
    await expect(page.getByTestId("disclaimer").first()).toBeVisible();
  }
});

test("2. approving one action changes only that card", async ({ page, request }) => {
  const before = (await (await request.get("/api/actions?status=pending&limit=500")).json()).items as Array<{ id: string; kind: string }>;
  const target = before.find((a) => a.kind === "reply_send") ?? before[0];
  if (!target) throw new Error("no pending actions");

  await page.goto("/approvals");
  const card = page.locator(`article[id="${target.id}"]`);
  await expect(card.getByText("matches")).toBeVisible();
  await card.getByRole("button", { name: "Approve" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("This will write to");
  await dialog.getByRole("button", { name: /Approve and (send|write)/ }).click();
  await expect(card).toHaveCount(0);

  const after = (await (await request.get("/api/actions?status=&limit=500")).json()).items as Array<{ id: string; status: string }>;
  for (const a of before) {
    const now = after.find((x) => x.id === a.id);
    if (a.id === target.id) expect(now?.status).toBe("executed");
    else expect(now?.status, `action ${a.id} must stay pending`).toBe("pending");
  }
});

test("3. a blocked ticket has no Send button in the DOM", async ({ page, request }) => {
  const id = await createBlockedTicket(request);
  await page.goto(`/queue/${id}`);
  await expect(page.getByText("Blocked by reviewer after 1 rework round")).toBeVisible();
  await expect(page.getByTestId("approve-send")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Approve & send/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit" })).toBeEnabled();
});

test("4. the confirm dialog names the destination", async ({ page, request }) => {
  const id = await createOpenTicket(request);
  await page.goto(`/queue/${id}`);
  await page.getByTestId("approve-send").click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(`Outbox · ticket ${id}`);
  await expect(dialog.locator("pre")).toContainText("Exit load");
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("5. Refine is disabled at 5 of 5", async ({ page, request }) => {
  const id = await createOpenTicket(request, "What is the exit load on HDFC Mid Cap?");
  for (let i = 0; i < 5; i++) {
    const res = await request.post(`/api/tickets/${id}/refine`, { data: { kind: "other", hint: "" } });
    expect(res.ok()).toBe(true);
  }
  expect((await request.post(`/api/tickets/${id}/refine`, { data: { kind: "other", hint: "" } })).status()).toBe(409);
  await page.goto(`/queue/${id}`);
  await expect(page.getByRole("button", { name: /Refine \(limit of 5 reached\)/ })).toBeDisabled();
  await expect(page.getByText("Refinements 5 / 5")).toBeVisible();
});

test("6. read-only gate mode pauses doc/email approvals but not replies", async ({ page }) => {
  // The gate's read-only flag comes from /api/health; simulate the MCP writer being down.
  await page.route("**/api/health", async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    await route.fulfill({ response: res, json: { ...body, gate_mode: "read_only" } });
  });
  await page.goto("/approvals");
  await expect(page.getByText(/Gate is read-only/)).toBeVisible();
  const doc = page.locator("article").filter({ has: page.getByText("Doc append", { exact: true }) }).first();
  const reply = page.locator("article").filter({ has: page.getByText("Reply send", { exact: true }) }).first();
  await expect(doc.getByRole("button", { name: "Approve" })).toBeDisabled();
  await expect(reply.getByText("matches")).toBeVisible();
  await expect(reply.getByRole("button", { name: "Approve" })).toBeEnabled();
});

test("7. every rendered link is a corpus/edu URL or an internal route", async ({ page, request }) => {
  const sources = await (await request.get("/api/sources")).json();
  const health = await (await request.get("/api/health")).json();
  const allowed = new Set<string>([...sources.blocks.map((b: { source_url: string }) => b.source_url), ...health.edu_urls]);
  const reuse = await createOpenTicket(request, "Why was I charged an exit load on HDFC Large Cap?");
  for (const route of [...ROUTES, "/sources/drift", `/queue/${reuse}`]) {
    await page.goto(route);
    await settle(page);
    const hrefs = await page.locator("a[href]").evaluateAll((els) => els.map((e) => e.getAttribute("href") ?? ""));
    for (const href of hrefs) {
      if (href.startsWith("/") || href.startsWith("#")) continue;
      expect(allowed.has(href), `${route}: unexpected link ${href}`).toBe(true);
    }
  }
});

test("8. no serious or critical accessibility violations", async ({ page, request }) => {
  const id = await createOpenTicket(request);
  for (const route of [...ROUTES, "/sources/drift", `/queue/${id}`]) {
    await page.goto(route);
    await settle(page);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${route}: ${v.id} (${v.nodes.length}) ${v.nodes[0]?.target}`)).toEqual([]);
  }
  // The approval dialog too.
  await page.goto(`/queue/${id}`);
  await page.getByTestId("approve-send").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const dialog = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
  expect(dialog.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
});

test("9. screenshots for side-by-side review with the Stitch designs", async ({ page, request }) => {
  test.skip(!process.env.SCREENSHOTS, "set SCREENSHOTS=1 to refresh ui/tests/__screenshots__");
  const blocked = await createBlockedTicket(request);
  const open = await createOpenTicket(request, "Why was I charged an exit load on HDFC Large Cap? Call me on 9876543210.");
  const shots: Array<[string, string]> = [
    ["queue", `/queue/${open}`],
    ["queue-blocked", `/queue/${blocked}`],
    ["pulse", "/pulse"],
    ["sources", "/sources"],
    ["sources-drift", "/sources/drift"],
    ["approvals", "/approvals"],
  ];
  for (const [name, route] of shots) {
    await page.goto(route);
    await settle(page);
    await page.screenshot({ path: `tests/__screenshots__/${name}.png` });
  }
  // Show the explainers view with an active canonical explainer and one reuse, not the
  // empty state: approve the fee explainer's doc append, then ask a covered fee question.
  const pending = await (await request.get("/api/actions?status=pending&limit=500")).json();
  const explainer = pending.items.find(
    (a: { kind: string; subject_type: string }) => a.kind === "doc_append" && a.subject_type === "fee_explainer",
  );
  expect(explainer).toBeTruthy();
  expect((await request.post(`/api/actions/${explainer.id}/approve`)).ok()).toBe(true);
  await createOpenTicket(request, "How much exit load is charged on HDFC Mid Cap Fund if I redeem early?");
  await page.goto("/explainers");
  await settle(page);
  await page.screenshot({ path: "tests/__screenshots__/explainers.png" });
});
