// All fixed UI copy (plan P9.2). Nothing here is sample data from the Stitch export:
// facts, URLs, counts and dates always come from the API.

export const DISCLAIMER = "Facts-only. No investment advice.";

export const NAV = [
  { to: "/queue", label: "Queue" },
  { to: "/pulse", label: "Weekly pulse" },
  { to: "/sources", label: "Sources" },
  { to: "/approvals", label: "Approvals" },
  { to: "/explainers", label: "Canonical explainers" },
] as const;

export const PAGE = {
  queue: { title: "Queue", subtitle: "Incoming questions with AI drafts. Nothing is sent without your approval." },
  pulse: { title: "Weekly pulse", subtitle: "Themes from Play Store reviews, plus repeat questions from the support queue." },
  sources: { title: "Sources", subtitle: "Where every factual claim comes from. Newest snapshot wins." },
  approvals: {
    title: "Approvals",
    subtitle: "Each card shows exactly what will be written and where. Approving one action never triggers another.",
  },
  explainers: { title: "Canonical explainers", subtitle: "Approved explainers are reused verbatim for matching fee questions." },
} as const;

export const TICKET_STATUS: Record<string, string> = {
  processing: "Processing",
  awaiting_human: "Awaiting review",
  blocked: "Blocked",
  sent: "Sent",
  rejected: "Rejected",
  failed: "Failed",
};

export const ACTION_STATUS: Record<string, string> = {
  pending: "Pending approval",
  approved: "Approved",
  executing: "Writing…",
  executed: "Written",
  rejected: "Rejected",
  superseded: "Superseded",
  failed: "Failed",
};

export const ACTION_KIND: Record<string, string> = {
  doc_append: "Doc append",
  email_draft: "Email draft",
  reply_send: "Reply send",
};

export const SUBJECT: Record<string, string> = {
  pulse: "Pulse",
  fee_explainer: "Explainer",
  explainer: "Explainer",
  ticket: "Ticket",
};

export const REFINE_KINDS = [
  { kind: "shorter", label: "Shorter" },
  { kind: "different_scheme", label: "Different scheme" },
  { kind: "check_fee_issue", label: "Check fee issue" },
  { kind: "other", label: "Other…" },
] as const;

/** Reviewer checks in the order of arch §8.6. Codes match reviewer_result.failures. */
export const REVIEWER_CHECKS = [
  { code: "UNCITED_CLAIM", label: "Uncited claim" },
  { code: "ADVICE", label: "Advice" },
  { code: "BUY_SELL", label: "Buy / sell" },
  { code: "PERFORMANCE", label: "Performance" },
  { code: "PII", label: "PII" },
  { code: "LENGTH", label: "Length ≤ 3 sentences" },
  { code: "URL_SCOPE", label: "URL scope" },
  { code: "MISSING_LAST_CHECKED", label: "Last checked", soft: true },
] as const;

export const CAPS = { sentences: 3, pulseWords: 250, explainerBullets: 6, refinements: 5 } as const;

export const GATE_READ_ONLY =
  "Gate is read-only: the MCP writer is unavailable. Doc and email approvals are paused; reply approvals still work.";

export const NO_CITATION = "No factual claim, no citation needed";

/** Fixed explainer header scope line (arch §7.7, edge-case G4). */
export const EXPLAINER_SCOPE =
  "Scheme-level charges from the official scheme pages. Brokerage and trading charges are not covered here.";

/** Canonical section order (arch §5.1). */
export const SECTIONS = [
  "Key fund facts",
  "Exit load",
  "Stamp duty and tax",
  "Minimum investments",
  "Fund management",
  "Benchmark",
  "Investment objective",
] as const;
