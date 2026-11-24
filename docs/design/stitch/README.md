# Stitch design export (reference only)

Exported from Google Stitch on 2026-11-25 (decision **D6**, plan P9). Each folder has
`screen.png` (the design) and `code.html` (static HTML on the Tailwind CDN).
`support_desk_operations_console/DESIGN.md` holds the tokens, type scale and component specs.

**Nothing in `ui/` imports from this folder.** The React app in `ui/` rebuilds these layouts
with live API data. Tokens live in `ui/src/styles/tokens.css`.

## Screen → route

| Stitch screen | Route |
| --- | --- |
| `support_desk_queue`, `support_desk_queue_master_detail` | `/queue`, `/queue/:ticketId` |
| `support_desk_queue_blocked_ticket` | `/queue/:ticketId` (status `blocked`) |
| `support_desk_queue_not_covered_refinement_cap` | `/queue/:ticketId` (not covered, refinements 5/5) |
| `support_desk_weekly_pulse`, `…_weekly_pulse_empty_mcp_health_banner` | `/pulse`, `/pulse/:runId` |
| `support_desk_sources_corpus`, `support_desk_sources_drift` | `/sources`, `/sources/drift` |
| `support_desk_approvals`, `…_approvals_empty_mcp_health_banner` | `/approvals` |
| `support_desk_canonical_explainers` | `/explainers` |

## Audit (2026-11-25): don't copy these

| # | Finding | How `ui/` handles it |
| --- | --- | --- |
| S1 | Invented copy: an LTCG rule, "Regular plan 1.02% p.a.", "SEBI Gazette", a UPI-delay story, `leadership-briefing@company.internal`, `console.internal`, "cryptographic sign-off", "Zero hallucination guarantee", "Audit Throughput 98.4%", "KYC/AML Vetting", "Cannot be undone" | All text comes from the API or `ui/src/copy.ts`. `ui/scripts/check-content.mjs` fails the build on these strings. |
| S2 | Wrong scheme URLs (`hdfc-top-100-fund`, `hdfc-taxsaver`, `hdfc-mid-cap-opportunities-fund`, `hdfc-gold-fund`, `hdfc-silver-etf-fund-of-fund`, bare `groww.in`) | `CitationLink` links only API URLs on `PUBLIC_DOMAINS`. The e2e test checks every `a[href]` on every route. |
| S3 | Two token sets (Material-3 inline config vs `DESIGN.md` prose) | One set, from the prose. Metadata text uses slate-500, not slate-400, for WCAG AA. |
| S4 | No monospace fallback, so chips render in serif. Fonts and icons are loaded from Google at runtime. | Self-hosted `@fontsource` fonts with full fallback stacks, and `lucide-react` icons. |
| S5 | Scrollbars hidden; almost no ARIA; status shown by colour only | Visible scrollbars, focus rings, labelled controls, status as dot + text. axe runs on every route in e2e. |
| S6 | Light mode only, Tailwind from a CDN, no behaviour | Built Tailwind; all behaviour written. Dark mode is out of scope (plan R8). |
