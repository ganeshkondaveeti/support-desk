// Response types: generated from docs/openapi.json (npm run gen:api), plus narrow
// shapes for the fields the backend types as `Any` (taken from real API output).
import type { components } from "./types.gen";

type S = components["schemas"];

/**
 * FastAPI always serializes every response field, but fields with defaults come out
 * optional in OpenAPI. `Filled` makes response types match what the API actually sends.
 */
export type Filled<T> = T extends (infer U)[]
  ? Filled<U>[]
  : T extends object
    ? { [K in keyof T]-?: Filled<Exclude<T[K], undefined>> }
    : T;
type R<K extends keyof S> = Filled<S[K]>;

export type Health = R<"HealthResponse">;
export type TicketListItem = R<"TicketListItem">;
export type TicketList = R<"TicketListResponse">;
export type TicketDetail = R<"TicketDetailResponse">;
export type TicketCreated = R<"TicketCreateResponse">;
export type SeedResponse = R<"TicketSeedResponse">;
export type DraftVersion = R<"DraftVersionOut">;
export type AgentOutput = R<"AgentOutputOut">;
export type Refinement = R<"RefinementOut">;
export type WeeklyRunSummary = R<"WeeklyRunSummary">;
export type WeeklyRunList = R<"WeeklyRunListResponse">;
export type WeeklyRunDetail = R<"WeeklyRunDetail">;
export type WeeklyRunResponse = R<"WeeklyRunResponse">;
export type Theme = R<"ThemeOut">;
export type Pulse = R<"PulseOut">;
export type Explainer = R<"ExplainerOut">;
export type SourceBlock = R<"SourceBlockOut">;
export type SourcesResponse = R<"SourcesResponse">;
export type DriftRow = R<"DriftRowOut">;
export type Action = R<"ActionOut">;
export type ActionList = R<"ActionListResponse">;
export type CanonicalExplainer = R<"CanonicalExplainerOut">;
export type CanonicalExplainers = R<"CanonicalExplainersResponse">;

export type RefineKind = S["TicketRefineRequest"]["kind"];
export type TicketStatus = "processing" | "awaiting_human" | "blocked" | "sent" | "rejected" | "failed";
export type ActionStatus = "pending" | "approved" | "executing" | "executed" | "rejected" | "superseded" | "failed";
export type ActionKind = "doc_append" | "email_draft" | "reply_send";

export interface Quote {
  review_id: string;
  text: string;
}

export interface RepeatQuestion {
  representative_text: string;
  count: number;
  window?: string;
  window_start?: string;
  window_end?: string;
  theme_id?: string;
  possible_duplicate_burst?: boolean;
}

export interface CitedBullet {
  text: string;
  source_url: string;
  scheme_id?: string;
  section?: string;
  last_checked?: string;
  snapshot?: string;
}

export interface ReuseEntry {
  ticket_id: string;
  ts: string;
  origin?: string;
}

export interface ReviewerResult {
  status?: "pass" | "blocked" | "fail" | string;
  failures?: string[];
  warnings?: string[];
  flags?: string[];
  failed_version?: number;
}

export interface RoutingNote {
  team?: string;
  priority?: string;
  why?: string;
  multi_scheme?: string[];
  evidence?: {
    review_ids_sample?: string[];
    in_week_repeats?: number;
    theme_id?: string;
    trend?: string;
    review_window?: [string, string];
    ticket_window?: [string, string];
  };
}

export interface FactsSearched {
  schemes?: string[];
  sections?: string[];
  snapshots?: string[];
}
