# Google Stitch prompts: Support Desk UI

How to use these with https://stitch.withgoogle.com/:

1. Choose **Web** and the best model available (Experimental / Gemini Pro mode).
2. Paste **Prompt 0** first. It sets up the app shell and the design system.
3. In the same project, paste Prompts 1–5 one at a time, one screen per prompt. Stitch does better building screen by screen than from one huge prompt.
4. Paste Prompts 6–8 to get the state variants the evals depend on.
5. Export: **Copy to Figma** or **Code** (HTML + Tailwind). Stitch doesn't output React or Next.js directly. Port the exported markup into `ui/` as React + Vite + Tailwind components (plan P9.1). The architecture uses Vite, served by FastAPI at `/`. Next.js would need a separate server, so keep Vite.

All sample data below is real project data or redacted, except the theme trends, ticket times and IDs, which are made up for the mock-ups. Don't add real names, emails or phone numbers when iterating.

---

## Prompt 0: app shell and design system

```
Design a desktop web app called "Support Desk": an internal operations console used by one human reviewer at an Indian investment platform's support team. It answers customer questions about six HDFC Mutual Fund schemes using only facts from official scheme pages, and turns app-store reviews into a weekly product report. AI agents draft everything; a human must approve every single write or send. The design must communicate trust, auditability and calm control, not marketing.

Style: modern, dense-but-readable enterprise console (think Linear, Vercel dashboard, Stripe dashboard). Neutral slate/zinc greys, white surfaces, one deep teal-green primary accent (#0F766E) for primary actions and links. Semantic colours: amber for warnings/drift, red for blocked/hard-fail, green for passed checks, blue for informational. Inter font, 14px base, tabular numerals for counts and dates. 8px spacing grid, 8px corner radius, 1px hairline borders, very soft shadows. Generous use of small pill badges and monospace chips for IDs (e.g. "rev_01J…", "tkt_01J…"). Light mode primary, must also work in dark mode. No stock photos, no illustrations, no third-party logos.

App shell (identical on every screen):
- Top header, full width, sticky. Left: product wordmark "Support Desk" with a small shield icon. Centre/left: a permanent, always-visible compliance banner pill reading exactly "Facts-only. No investment advice." (neutral amber-tinted background, info icon, cannot be dismissed).
- Header right: "Corpus last checked: A 28-Jun-2026 · B 01-Sep-2026" in muted text; a bell-style badge "Approvals 5" in the accent colour; an avatar menu "Reviewer".
- Left sidebar navigation with icons, 5 items: Queue, Weekly pulse, Sources, Approvals (with count badge 5), Canonical explainers. Collapsed state shows icons only.
- Main content area with a page title, a one-line subtitle, and a right-aligned primary action slot.

For this first screen, show the shell with the Queue item active and an empty-state main area: "No tickets yet" with buttons "Submit a question" and "Load seed questions".
```

---

## Prompt 1: Queue (list + detail)

```
Using the same app shell and design system, design the "Queue" screen: a master-detail layout.

Top: page title "Queue", subtitle "Incoming questions with AI drafts. Nothing is sent without your approval." Primary button "Submit question" (opens a textarea modal with the hint "Personal details like PAN, phone or email are redacted automatically"). Secondary button "Load seed questions". Filter chips: Status (All, Awaiting review, Blocked, Sent, Rejected), Team, Priority (P1, P2, P3).

Left (60%): a compact data table with columns: Received, Question (redacted, truncated to 1 line), Status pill, Priority pill, Team, Repeats (7-day count), Reviewer flags. Rows:
1. 24-Nov 10:42 | "Why was I charged an exit load? My PAN is [PAN], call me on [PHONE]. Should I just redeem everything?" | Awaiting review | P1 | Compliance | 7 | 0 flags. Redaction tokens like [PAN] and [PHONE] render as small grey monospace chips. This row is selected.
2. 24-Nov 10:15 | "What is the expense ratio of HDFC Mid Cap Fund?" | Awaiting review | P3 | Support | 1 | 0
3. 24-Nov 09:58 | "Which fund will give the best returns next year?" | Awaiting review | P2 | Compliance | 2 | 0
4. 23-Nov 18:20 | "Is there a lock-in for HDFC ELSS Tax Saver?" | Sent | P3 | Support | 1 | 0
5. 23-Nov 17:02 | "My withdrawal is stuck for 3 days" | Awaiting review | P2 | Payments Ops · knowledge gap | 3 | 0
6. 23-Nov 16:40 | "Exit load on gold fund? guaranteed 12% right?" | Blocked | P1 | Compliance | 7 | 2 flags (red)

Right (40%): a sticky detail pane for row 1, with stacked sections:
- Header: ticket id chip "tkt_01JD8…", status, P1, team "Compliance + Support/Product (Pricing)".
- Disagreement banner (amber, full width): "Facts agent has a clean, cited answer, but Pattern shows 7 similar questions this week (rising). Ticket stays open after sending — escalated to Product (Pricing)."
- Draft reply card with a "2 / 3 sentences" counter: "We can't offer investment advice or recommend whether to buy, hold or redeem. For general guidance on mutual funds, please see the AMFI investor education page. Exit load of 1% if redeemed within 1 year." Under it: citation link "groww.in · HDFC Large Cap Fund – Exit load" with an external-link icon, and the meta line "Last checked 01-Sep-2026 · Snapshot B". Origin chip: "template + corpus".
- Routing note (internal, muted card with a lock icon "Internal — never sent"): "fees_brokerage: 136 reviews 12-Apr→04-Jul-2026, recurring; 7 similar questions in last 7 days of tickets (18-Nov→24-Nov-2026)." Two clearly separated labels: "Review window" and "Ticket window".
- Reviewer checks: a compact checklist of 7 rows with green ticks: Uncited claim, Advice / buy-sell, Performance, PII, Length ≤3, URL scope, Last checked.
- Agent outputs (collapsible): 3 small cards side by side — Facts: scheme "—", section "Exit load"; Pattern: theme "fees_brokerage", trend "recurring"; Compliance: "advice". Each shows "Model picked" vs "Validated" values.
- Version history (v1 agent, v2 human edit) and "Refinements 1 / 5" with a thin progress bar.
- Sticky action bar at the bottom: primary "Approve & send", secondary "Edit", ghost-danger "Reject", and a "Refine" split button whose menu has exactly: Shorter, Different scheme, Check fee issue, Other… (with an optional hint field).
```

---

## Prompt 2: Weekly pulse

```
Using the same app shell, design the "Weekly pulse" screen.

Top: title "Weekly pulse", subtitle "Themes from 1,650 Play Store reviews, plus repeat questions from the support queue." Right: a run picker dropdown "Run: week ending 04-Jul-2026 (run_01JD…)" and a primary button "Run weekly loop now".

Row of 4 KPI tiles: "Reviews analysed 1,650 (12-Apr → 04-Jul-2026)", "Themes 5", "Pulse 212 / 250 words", "Pending actions 4 (this run)". Every count must show the window it covers in small muted text.

Section "Themes": a table with columns Theme id (monospace), Name, Reviews, Avg rating (★), Trend (pill: rising / recurring / one-off), Window, Review ids (expand button). Rows:
- other | Other feedback | 709 | 3.5★ | recurring | 12-Apr→04-Jul-2026
- trading_charts | Trading, charts & orders | 503 | 2.7★ | recurring
- money_movement | Withdrawals, deposits & payments | 162 | 2.5★ | rising
- reliability | App stability & performance | 140 | 2.2★ | one-off
- fees_brokerage | Fees & brokerage | 136 | 2.2★ | recurring
Show the fees_brokerage row expanded, revealing a wrap of small monospace review-id chips ("rev_0142", "rev_0388", … "+128 more").

Two-column area below:
Left: "Pulse preview" rendered as a clean document card (paper-like, max-width ~70ch) with a word counter badge "212 / 250". Content: heading "Weekly Product Pulse — week ending 04-Jul-2026 (reviews 12-Apr → 04-Jul-2026)", the line "Facts-only. No investment advice.", "Top themes" (3 numbered themes, each with one short italic quote followed by a review-id chip), "Fee confusion: 136 reviews, recurring. Most cited: charges, brokerage, hidden." with a note that brokerage is outside the scheme corpus, "Repeat questions from support (tickets 18-Nov → 24-Nov-2026)" with "Why was I charged an exit load? — 7×", and "Action ideas" (3 items).
Right: "Fee explainer preview" card with counter "6 / 6 bullets", a scope line "Scheme-level charges from the official scheme pages. Brokerage and trading charges are not covered here.", 6 bullets each with a tiny source chip (Large Cap / Gold FoF): "Exit load of 1% if redeemed within 1 year", "Exit load of 1%, if redeemed within 15 days.", "Stamp duty on investment: 0.005% (from July 1st, 2020).", "Expense ratio: 1.02%.", "Expense ratio: 0.20%.", "If you redeem within one year, returns are taxed at 20%." Footer: "Sources: 2 links · Last checked 01-Sep-2026".
Below both: a strip "This run proposed 4 actions" with 4 small linked cards (Pulse → Doc append, Pulse → Email draft, Explainer → Doc append, Explainer → Email draft), each with status "Pending approval" and a link "Review in Approvals".
```

---

## Prompt 3: Sources

```
Using the same app shell, design the "Sources" screen: where every factual claim comes from.

Top: title "Sources", subtitle "6 HDFC Mutual Fund schemes · 7 sections · two snapshots (A 28-Jun-2026, B 01-Sep-2026). Newest snapshot wins." Tabs: "Corpus" (active) and "Drift (4)". Filters: Scheme dropdown, Section dropdown, Snapshot segmented control (All / A / B).

Main: a grouped list, grouped by scheme (HDFC Large Cap Fund, HDFC Mid Cap Fund, HDFC Small Cap Fund, HDFC Gold ETF FoF, HDFC Silver ETF FoF, HDFC ELSS Tax Saver Fund). Each scheme header shows the source URL domain "groww.in" with external link icon and which snapshots exist (A, B badges). Under each, rows for the 7 sections: Key fund facts, Exit load, Stamp duty and tax, Minimum investments, Fund management, Benchmark, Investment objective. Each row: section name, the verbatim resolved text (quote-styled, 1–2 lines), snapshot badge (B in teal, A in grey), NAV date, Last checked, and "Cited by 3" count.
Show HDFC Large Cap Fund expanded, with the "Exit load" row opened into a detail drawer that shows: Resolved (B): "Exit load of 1% if redeemed within 1 year"; Other snapshot (A): same text with a green "identical" tag; "Original B content before normalization" in a monospace block; "Quarantined sentences" in a red-tinted list with a reason ("AMC-wide AUM figure, same on every scheme — never quoted"); and "Cited by": 2 ticket chips and 1 explainer bullet chip.
For HDFC ELSS Tax Saver Fund show the Benchmark and Investment objective rows greyed out as "Not covered in corpus".

Also design the "Drift" tab as a second frame: a table Scheme | Expense ratio A → B | Fund size A → B | Riskometer A → B | Exit load. Rows: Large Cap 1.04% → 1.02% | ₹37,808.31 Cr → ₹40,197.89 Cr | Moderately High → Very High | unchanged; Mid Cap 0.75% → 0.74% | ₹97,350.48 Cr → ₹1,05,142.69 Cr | Moderately High → Very High | unchanged; Small Cap 0.77% → 0.75% | ₹38,809.48 Cr → ₹41,679.00 Cr | Moderately High → Very High | unchanged; Gold FoF 0.2% → 0.20% | ₹12,121.18 Cr → ₹11,197.05 Cr | not in B | unchanged. Changed values use an amber arrow; the info note reads "Drift is informational. Exit loads are identical across snapshots."
```

---

## Prompt 4: Approvals

```
Using the same app shell, design the "Approvals" screen: the human gate. Nothing is written or sent until the reviewer approves that one specific action.

Top: title "Approvals", subtitle "Each card shows exactly what will be written and where. Approving one action never triggers another." Tabs: Pending (5), Approved, Rejected. No bulk-select checkboxes anywhere, no "Approve all" button.

Cards grouped by kind with group headers: "Doc append (2)", "Email draft (2)", "Reply send (1)". Each card is a full-width bordered card with:
- Kind badge with icon (document / mail / paper-plane), action id chip "act_01JD…", created time, source ("Weekly run run_01JD…" or "Ticket tkt_01JD…").
- "Destination" block, very prominent: for doc append "Google Doc · Weekly Product Pulse log · heading: Week ending 04-Jul-2026"; for email draft "Gmail draft · To: product-team@ (internal list) · Subject: Weekly Product Pulse — 04-Jul-2026"; for reply send "Outbox · ticket tkt_01JD8…".
- "Exact payload" block: the full text exactly as it will be written, in a scrollable bordered panel with a monospace header showing "sha256 3f9a…c21e" and a copy button.
- "What else happens" callout: for the explainer doc append, an amber callout "Approving this makes Explainer #E3 the canonical fee answer. Future exit-load questions will reuse its bullets." For the others: "Nothing else. This action only."
- Footer actions on each card: primary "Approve", ghost-danger "Reject".
One reply-send card also shows a red-outlined note "This reply replaces a draft that failed review" with the original flags as chips: "UNCITED_CLAIM", "PERFORMANCE".

Also design the confirm dialog that opens on Approve: title "Approve this action?", body names the destination explicitly ("This will append 1 section to Google Doc 'Weekly Product Pulse log' under heading 'Week ending 04-Jul-2026'."), shows the payload hash, and has buttons "Cancel" and "Approve and write" (primary). No "don't ask again" option.
```

---

## Prompt 5: Canonical explainers

```
Using the same app shell, design the "Canonical explainers" screen: the approved fee explainers the reply agents reuse.

Top: title "Canonical explainers", subtitle "Approved explainers are reused verbatim for matching fee questions."

Hero card "Active explainer #E3" with a green "Canonical" badge: approved by "Reviewer" on 24-Nov-2026 11:05, link "View approval record (act_01JD…)". Covers: schemes "Large Cap, Gold FoF" + same family; sections "Exit load, Stamp duty and tax, Key fund facts". The scope line "Scheme-level charges from the official scheme pages. Brokerage and trading charges are not covered here." Then 6 numbered bullets, each with its own small source link chip and "Snapshot B":
1. Exit load of 1% if redeemed within 1 year
2. Exit load of 1%, if redeemed within 15 days.
3. Stamp duty on investment: 0.005% (from July 1st, 2020).
4. Expense ratio: 1.02%.
5. Expense ratio: 0.20%.
6. If you redeem within one year, returns are taxed at 20%.
Footer: two source URLs (groww.in HDFC Large Cap Fund, groww.in HDFC Gold ETF FoF) and "Last checked 01-Sep-2026".

Right column "Reuse log": a vertical timeline of tickets whose reply came from this explainer: "tkt_01JD8… · 24-Nov 11:20 · bullet 1 · Sent", "tkt_01JD9… · 24-Nov 11:42 · bullets 1, 3 · Awaiting review", each clickable, with "Reuse count 2".

Below: "History" table of superseded explainers: #E2 (superseded 24-Nov-2026), #E1 (superseded 17-Nov-2026) with approved-by, date range active, reuse count, and a "View" link.
```

---

## Prompt 6: blocked ticket state

```
Variant of the Queue detail pane for a ticket with status "Blocked" (row: "Exit load on gold fund? guaranteed 12% right?"). Show a red banner at the top: "Blocked by reviewer after 1 rework round. This draft cannot be sent." The reviewer checklist shows 2 red failures, "UNCITED_CLAIM — 'guaranteed 12%' has no corpus source" and "PERFORMANCE — return figure", and the other 5 checks green. The "Approve & send" button is completely absent (not just disabled). The remaining actions are "Edit" (primary) and "Reject". A helper text says "Edit the draft to re-run the reviewer. A passing edit creates a new reply for approval."
```

---

## Prompt 7: not-covered reply and refinement cap

```
Variant of the Queue detail pane for "My withdrawal is stuck for 3 days". The draft reads: "This isn't covered by the scheme information we hold. We searched: 6 schemes — sections: 7 sections (scheme pages as of 28-Jun-2026 and 01-Sep-2026)." Show an expandable "Searched" list naming the 6 schemes and 7 sections as chips. There is no citation link; show a neutral "No factual claim, no citation needed" tag. Team "Payments Ops · knowledge gap", P2. Refinements show "5 / 5" with a full progress bar, and the Refine button is disabled with the tooltip "Refinement limit reached (5)".
```

---

## Prompt 8: health and empty states

```
Design 3 small states for the same app:
1. A slim warning bar under the header: "Gate is read-only: the MCP writer is unavailable. Doc and email approvals are paused; reply approvals still work." (amber, with a "Retry" link).
2. Weekly pulse empty state: "No weekly run yet" with the button "Run weekly loop now" and a short note "Runs automatically every Monday 09:00 IST".
3. Approvals empty state: "Nothing waiting for approval" with a muted check illustration made of simple shapes and the line "Every write and send needs your approval. They'll appear here."
```
