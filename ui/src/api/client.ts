import { getToken, requestToken } from "./auth";
import type {
  Action,
  ActionList,
  CanonicalExplainers,
  Health,
  RefineKind,
  SeedResponse,
  SourcesResponse,
  TicketCreated,
  TicketDetail,
  TicketList,
  WeeklyRunDetail,
  WeeklyRunList,
  WeeklyRunResponse,
} from "./types";

// Same origin by default (FastAPI serves the UI). An absolute base keeps fetch working in tests too.
const BASE = (import.meta.env.VITE_API_BASE_URL || (typeof window !== "undefined" ? window.location.origin : "")).replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${BASE}/api${path}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * Map a failed response to a message the UI can show. Validation errors carry only
 * `loc`/`type`/`msg` (arch §12, G6), so nothing the reviewer typed is echoed back.
 */
export async function toApiError(response: Response): Promise<ApiError> {
  if (response.status === 401) requestToken();
  let code = `http_${response.status}`;
  let message = `The request failed (${response.status}).`;
  try {
    const body = (await response.json()) as { detail?: unknown; code?: string };
    if (typeof body.code === "string") code = body.code;
    if (typeof body.detail === "string") {
      message = body.detail;
      if (/refinement_cap_reached|already has \d+ refinements/i.test(body.detail)) code = "refinement_cap_reached";
    } else if (body.detail && typeof body.detail === "object" && !Array.isArray(body.detail)) {
      // Weekly run lock: {message, run_id} (arch §11).
      const detail = body.detail as { message?: string; run_id?: string };
      if (detail.run_id) {
        code = "weekly_run_locked";
        message = `Run already in progress (${detail.run_id}).`;
      } else if (detail.message) {
        message = detail.message;
      }
    } else if (Array.isArray(body.detail) && body.detail.length > 0) {
      const first = body.detail[0] as { loc?: unknown[]; msg?: string };
      const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "request";
      code = "validation_error";
      message = `Invalid ${field}.`;
    }
  } catch {
    // Not JSON: keep the generic message.
  }
  if (response.status === 401) message = "Reviewer token required or rejected.";
  return new ApiError(response.status, code, message);
}

async function request<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token && init.method && init.method !== "GET") headers.Authorization = `Bearer ${token}`;
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    throw new ApiError(0, "network_error", "The API is unreachable.");
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") search.set(key, String(value));
  const out = search.toString();
  return out ? `?${out}` : "";
}

const id = (value: string) => encodeURIComponent(value);

export interface TicketFilters {
  status?: string;
  team?: string;
  priority?: string;
}

export const api = {
  health: () => request<Health>("/health"),

  tickets: (filters: TicketFilters = {}) => request<TicketList>(`/tickets${query({ ...filters, limit: 200 })}`),
  ticket: (ticketId: string) => request<TicketDetail>(`/tickets/${id(ticketId)}`),
  createTicket: (text: string) => request<TicketCreated>("/tickets", { method: "POST", body: { text, channel: "ui" } }),
  seedTickets: () => request<SeedResponse>("/tickets/seed", { method: "POST" }),
  editTicket: (ticketId: string, text: string) =>
    request<TicketDetail>(`/tickets/${id(ticketId)}/edit`, { method: "POST", body: { text } }),
  rejectTicket: (ticketId: string) => request<TicketDetail>(`/tickets/${id(ticketId)}/reject`, { method: "POST", body: {} }),
  refineTicket: (ticketId: string, kind: RefineKind, hint: string) =>
    request<TicketDetail>(`/tickets/${id(ticketId)}/refine`, { method: "POST", body: { kind, hint } }),

  weeklyRuns: () => request<WeeklyRunList>("/weekly/runs?limit=50"),
  weeklyRun: (runId: string) => request<WeeklyRunDetail>(`/weekly/runs/${id(runId)}`),
  runWeekly: () => request<WeeklyRunResponse>("/weekly/run", { method: "POST" }),

  sources: () => request<SourcesResponse>("/sources"),

  /** `status: "all"` sends `status=` (empty), which the API treats as no filter; omitting it means "pending". */
  actions: (status: string) =>
    request<ActionList>(`/actions?status=${status === "all" ? "" : encodeURIComponent(status)}&limit=500`),
  action: (actionId: string) => request<Action>(`/actions/${id(actionId)}`),
  approveAction: (actionId: string) => request<Action>(`/actions/${id(actionId)}/approve`, { method: "POST" }),
  rejectAction: (actionId: string) => request<Action>(`/actions/${id(actionId)}/reject`, { method: "POST" }),

  canonicalExplainers: () => request<CanonicalExplainers>("/explainers/canonical"),
};
