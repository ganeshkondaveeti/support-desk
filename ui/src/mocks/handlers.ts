import { http, HttpResponse } from "msw";

import type { Action, CanonicalExplainers, Health, SourcesResponse, TicketDetail, TicketList, WeeklyRunDetail, WeeklyRunList } from "../api/types";
import raw from "./fixtures/api.json";

// Fixtures are real API responses from a local seeded run (redacted, PII-swept).
interface Fixtures {
  health: Health;
  tickets: TicketList;
  ticketDetails: Record<string, TicketDetail>;
  runs: WeeklyRunList;
  runDetail: WeeklyRunDetail;
  sources: SourcesResponse;
  actions: { items: Action[]; total: number };
  explainers: CanonicalExplainers;
}

export function freshFixtures(): Fixtures {
  return structuredClone(raw as unknown as Fixtures);
}

let db = freshFixtures();
export function resetMockDb(): void {
  db = freshFixtures();
}
export function mockDb(): Fixtures {
  return db;
}

function detailFor(id: string): TicketDetail | undefined {
  const known = db.ticketDetails[id];
  if (known) return known;
  const item = db.tickets.items.find((t) => t.id === id);
  if (!item) return undefined;
  return { ...item, current_draft: null, drafts: [], agent_outputs: [], refinements: [], pending_reply_send_id: null } as TicketDetail;
}

export const handlers = [
  http.get("*/api/health", () => HttpResponse.json(db.health)),

  http.get("*/api/tickets", ({ request }) => {
    const url = new URL(request.url);
    const status = url.searchParams.get("status");
    const team = url.searchParams.get("team");
    const priority = url.searchParams.get("priority");
    const items = db.tickets.items.filter(
      (t) => (!status || t.status === status) && (!team || t.route_team === team) && (!priority || t.priority === priority),
    );
    return HttpResponse.json({ items, total: items.length });
  }),
  http.get("*/api/tickets/:id", ({ params }) => {
    const detail = detailFor(String(params.id));
    return detail ? HttpResponse.json(detail) : HttpResponse.json({ detail: "ticket not found" }, { status: 404 });
  }),
  http.post("*/api/tickets/:id/refine", ({ params }) => {
    const detail = detailFor(String(params.id));
    if (!detail) return HttpResponse.json({ detail: "ticket not found" }, { status: 404 });
    if (detail.refinement_count >= 5) {
      return HttpResponse.json({ detail: `ticket ${detail.id} already has 5 refinements` }, { status: 409 });
    }
    detail.refinement_count += 1;
    return HttpResponse.json(detail);
  }),

  http.get("*/api/weekly/runs", () => HttpResponse.json(db.runs)),
  http.get("*/api/weekly/runs/:id", () => HttpResponse.json(db.runDetail)),
  http.post("*/api/weekly/run", () =>
    HttpResponse.json({ detail: { message: "weekly run already in progress", run_id: db.runDetail.id } }, { status: 409 }),
  ),

  http.get("*/api/sources", () => HttpResponse.json(db.sources)),
  http.get("*/api/explainers/canonical", () => HttpResponse.json(db.explainers)),

  http.get("*/api/actions", ({ request }) => {
    const status = new URL(request.url).searchParams.get("status") ?? "pending";
    const items = db.actions.items.filter((a) => !status || a.status === status);
    return HttpResponse.json({ items, total: items.length });
  }),
  http.get("*/api/actions/:id", ({ params }) => {
    const action = db.actions.items.find((a) => a.id === params.id);
    return action ? HttpResponse.json(action) : HttpResponse.json({ detail: "not found" }, { status: 404 });
  }),
  http.post("*/api/actions/:id/:decision", ({ params }) => {
    const action = db.actions.items.find((a) => a.id === params.id);
    if (!action) return HttpResponse.json({ detail: "not found" }, { status: 404 });
    if (action.status !== "pending") {
      return HttpResponse.json({ detail: `action ${action.id} is '${action.status}'; only pending can be approved` }, { status: 409 });
    }
    action.status = params.decision === "approve" ? "executed" : "rejected";
    action.decided_by = "reviewer";
    action.decided_at = "2026-11-25T10:00:00";
    db.health.pending_approvals = Math.max(0, db.health.pending_approvals - 1);
    return HttpResponse.json(action);
  }),
];
