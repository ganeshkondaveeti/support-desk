import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const ROUTES = ["/queue", "/pulse", "/sources", "/approvals", "/explainers"] as const;

/** First-boot seeding runs in the background; wait until tickets and a weekly run exist. */
export async function waitForSeed(request: APIRequestContext): Promise<void> {
  await expect
    .poll(
      async () => {
        const health = await (await request.get("/api/health")).json();
        return Boolean(health.seeded && health.last_weekly_run);
      },
      { timeout: 60_000, intervals: [500] },
    )
    .toBe(true);
}

/** A ticket the reviewer blocks: the eval fault adds advice to every draft pass (APP_ENV=test). */
export async function createBlockedTicket(request: APIRequestContext): Promise<string> {
  const res = await request.post("/api/tickets", {
    data: { text: "What is the exit load on HDFC Large Cap?" },
    headers: { "X-Eval-Ticket": "advice_always" },
  });
  expect(res.ok()).toBe(true);
  const { id } = await res.json();
  const detail = await (await request.get(`/api/tickets/${id}`)).json();
  expect(detail.status).toBe("blocked");
  return id;
}

export async function createOpenTicket(request: APIRequestContext, text = "What is the exit load on HDFC Gold ETF FoF?"): Promise<string> {
  const res = await request.post("/api/tickets", { data: { text } });
  expect(res.ok()).toBe(true);
  return (await res.json()).id;
}

export async function settle(page: Page): Promise<void> {
  await page.waitForLoadState("networkidle");
}
