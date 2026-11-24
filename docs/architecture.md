# The Support Desk — Architecture

> **Facts-only. No investment advice.**
>
> Source brief: [problemStatement.md](problemStatement.md). Section references like "§5.2" point to that brief.

---

## 0. Document map

| # | Section | Answers |
| --- | --- | --- |
| 1 | [Goals and non-goals](#1-goals-and-non-goals) | What the system must and must not do |
| 2 | [System context](#2-system-context) | The two loops, the bridge and the gate, in one picture |
| 3 | [Technology choices](#3-technology-choices) | Stack and the reason for each pick |
| 4 | [Repository layout](#4-repository-layout) | Where each component lives |
| 5 | [Data layer](#5-data-layer) | Input files, persistent store and schemas |
| 6 | [Scope enforcement (guardrails)](#6-scope-enforcement-guardrails) | PII redaction, advice and performance validators, allow-lists |
| 7 | [Weekly loop](#7-weekly-loop) | Reviews → themes → pulse and fee explainer |
| 8 | [Response loop](#8-response-loop) | Question → 3 parallel agents → orchestrator → reviewer → human |
| 9 | [Connecting the loops](#9-connecting-the-loops) | Repeat questions → pulse; explainer → canonical reply |
| 10 | [Approval gate and MCP](#10-approval-gate-and-mcp) | How every write and send is gated |
| 11 | [Scheduling and automation](#11-scheduling-and-automation) | Cron, manual trigger, idempotency |
| 12 | [API surface](#12-api-surface) | HTTP endpoints |
| 13 | [User interface](#13-user-interface) | The five required views |
| 14 | [Observability and PII-safe logging](#14-observability-and-pii-safe-logging) | Logs, run records, audit trail |
| 15 | [Evaluation harness](#15-evaluation-harness) | How §8 is tested over the assembled system |
| 16 | [Deployment](#16-deployment) | Container, hosting, secrets |
| 17 | [Failure modes and known limits](#17-failure-modes-and-known-limits) | What breaks and how it degrades |
| 18 | [Requirements traceability](#18-requirements-traceability) | Each hard rule mapped to the code that enforces it |

---

## 1. Goals and non-goals

### 1.1 Goals

1. Run **two loops as one deployed system**:
   - **Weekly loop**: reviews → ≤5 themes → top 3 → fee confusion → pulse (≤250 words) + fee explainer (≤6 bullets).
   - **Response loop**: user question → PII redaction → Facts, Pattern and Compliance agents in parallel → Orchestrator → Reviewer → human.
2. **Connect the loops**: a question asked 5× in a week appears in the pulse, and an approved fee explainer becomes the canonical reply.
3. **Enforce scope in code** (regex, allow-lists, validators), not only in prompts.
4. **Gate every write and send** behind a human approving that one action.
5. **Evaluate the assembled system** for feasibility, correctness of edits, and grounding and compliance.

### 1.2 Non-goals

- No scraping or refreshing of scheme pages at runtime. Both corpus snapshots are frozen: `data/parsed/` (2026-06-28) and `data/processed/` (2026-09-01).
- No general-knowledge answers. If the corpus doesn't cover a question, the system answers "not covered".
- No auto-send of anything: no emails, no replies to users.
- No investment advice, return or performance figures, or buy/sell language anywhere.
- No real helpdesk integration. An approved reply "send" writes to an outbox. See §10.4.

### 1.3 Design principles

| Principle | Consequence in the design |
| --- | --- |
| **Models pick, code writes** | Every agent's model call returns an id or flag from a closed list. All user-visible text is assembled by code from the corpus, theme data or fixed templates. |
| **Redact at the door** | PII is removed at API ingress, before persistence, before any model call, and before any log line. Raw text is never stored, not even briefly. |
| **Deterministic checks for hard rules** | The Reviewer and all validators are pure code, so hard fails are reproducible and testable. |
| **One action, one approval** | Each write or send is a separate `ProposedAction` row, approved and executed individually. Approvals never cascade. |
| **Provenance on everything** | Every claim carries a `source_url` or `review_id`, and every count carries a `window`. |

---

## 2. System context

```mermaid
flowchart LR
  subgraph Inputs
    R[normalized_reviews.json<br/>1,650 Play Store reviews]
    C[Facts corpus: 6 HDFC schemes<br/>A: data/parsed/ · 2026-06-28<br/>B: data/processed/ · 2026-09-01]
    Q[Question queue<br/>UI / API / seed file]
  end

  subgraph Guardrails["Guardrails (shared, code-enforced)"]
    PII[PII redactor]
    VAL[Scope validators<br/>advice · performance · buy/sell]
    ALLOW[Allow-lists<br/>scheme_ids · sections · theme_ids · URLs]
  end

  subgraph Weekly["Weekly loop (scheduled + manual)"]
    W1[Ingest + redact] --> W2[Cluster ≤5 themes] --> W3[Top 3 + fee theme]
    W3 --> W4[Pulse writer]
    W3 --> W5[Fee explainer writer]
  end

  subgraph Response["Response loop (LangGraph)"]
    RD[Redact] --> FA[Facts agent]
    RD --> PA[Pattern agent]
    RD --> CA[Compliance agent]
    FA --> OR[Orchestrator]
    PA --> OR
    CA --> OR
    OR --> RV[Reviewer]
    RV -->|fail, once| OR
  end

  subgraph Gate["Approval gate"]
    PAQ[(proposed_actions)]
    HUM{{Human: approve / edit / reject}}
    EXE[Executor]
  end

  subgraph MCP["MCP server (TypeScript, reused from M3)"]
    DOC[google_docs_append]
    MAIL[gmail_draft]
  end

  R --> W1
  Q --> RD
  C --> FA
  C --> W5
  W4 --> PAQ
  W5 --> PAQ
  RV --> PAQ
  PAQ --> HUM --> EXE
  EXE --> DOC
  EXE --> MAIL
  EXE --> OUT[(reply outbox)]

  OR -. "repeat-question counts" .-> W4
  W5 -. "approved → canonical explainer" .-> OR

  PII -.-> W1
  PII -.-> RD
  VAL -.-> W4
  VAL -.-> RV
  ALLOW -.-> FA
  ALLOW -.-> PA
  ALLOW -.-> CA
```

**Reading the diagram:**

- Solid arrows carry data.
- Dotted arrows between loops are the **bridge** (§9).
- Dotted arrows from Guardrails show where each guard is applied.
- The only path out of the system to an external service is **Executor → MCP server**. The Executor runs only for a `ProposedAction` a human has approved.

---

## 3. Technology choices

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11+ | Same as Milestones 1–3, so their code carries over. |
| Agent graph | **LangGraph** (`StateGraph`) | Required by §5.2. Supports parallel fan-out and fan-in, conditional edges for reviewer rework, and checkpointing. |
| LLM access | Thin `LLMClient` interface with a provider adapter (configured by `LLM_PROVIDER` and `LLM_MODEL`) | Carries forward whichever provider Milestone 3 used. Every call goes through one function that enforces JSON-mode output, enum validation and a timeout. |
| API server | **FastAPI** + Uvicorn | Async, typed (Pydantic), and serves the built UI from the same process. |
| UI | **React + Vite + TypeScript**, built to static files served by FastAPI | One deployable. The five views (§6) are list and detail screens, so a small SPA is enough. |
| Persistence | **SQLite** via SQLAlchemy (Postgres-compatible schema) on a persistent volume | Zero-ops for a prototype. Switching to Postgres only requires changing `DATABASE_URL`. |
| Graph checkpointing | `langgraph.checkpoint.sqlite.SqliteSaver` (same DB file) | Supports refinement re-runs of single nodes. State holds only redacted text (§6.1). |
| Scheduler | **APScheduler** (`AsyncIOScheduler`, cron trigger) in-process, plus an authenticated manual trigger endpoint | Meets "on a schedule and on manual trigger" (§5.1) without extra infrastructure. |
| External writes | **MCP server reused from Milestone 3**: TypeScript, Node ≥ 22, `@modelcontextprotocol/sdk`, stdio transport, exposing `google_docs_append` and `gmail_draft` (no send tool). Vendored into `mcp-server/` with two additions (§10.4): an `idempotency_key` argument and a `local` backend. The Python side connects with the `mcp` Python SDK **client**. | Decision D1 (plan §4). The server, its Google OAuth flow and its tests already work in M3, so reusing them avoids a ~1-day port. Only the Executor holds an MCP client session. |
| Tests and evals | `pytest` + an `evals/` runner that writes JSON and Markdown reports | §8 evals run over the HTTP API against a real app instance. |
| Packaging and deploy | Docker (single image), hosted on Render / Railway / Fly.io / HF Spaces | Meets §9.2's hosted prototype requirement. |

---

## 4. Repository layout

```
Support-desk-Capstone-Project/
├── README.md                       # §9.1 deliverable
├── Dockerfile
├── pyproject.toml
├── .env.example                    # no secrets committed
├── data/
│   ├── parsed/                     # Corpus snapshot A, 2026-06-28 (read-only)
│   │   ├── parse_manifest.json
│   │   └── {large_cap,mid_cap,small_cap,gold_fof,silver_fof}.json
│   ├── processed/                  # Corpus snapshot B, 2026-09-01 (read-only, copied from RAG-Chatbot/backend/data)
│   │   └── hdfc_{large_cap_fund,mid_cap_fund,small_cap_fund,gold_etf_fof,elss_tax_saver_fund}.json
│   ├── raw/                        # Source HTML for snapshot B (2026-08-30, 2026-09-01), provenance only
│   ├── chroma_db/                  # Milestone 1 Chroma index (mf_facts, 23 chunks of B), not used at runtime
│   ├── normalized_reviews.json     # Milestone 2 reviews (read-only input)
│   ├── analyzed_themes.json        # Milestone 2 themes (regression baseline)
│   └── seed_questions.json         # demo / eval question queue (synthetic, PII planted for tests)
├── app/
│   ├── main.py                     # FastAPI app factory, static UI mount, scheduler start
│   ├── config.py                   # env settings (Pydantic BaseSettings)
│   ├── db/
│   │   ├── models.py               # SQLAlchemy tables (§5.3)
│   │   ├── session.py
│   │   └── snapshots.py            # state snapshot + diff (used by evals §15.2)
│   ├── guardrails/
│   │   ├── pii.py                  # redact(), scan()  (§6.1)
│   │   ├── scope.py                # advice / performance / buy-sell validators (§6.2)
│   │   ├── allowlists.py           # scheme_ids, sections, theme_ids, URL allow-list (§6.3)
│   │   ├── length.py               # sentence / word / bullet counters (§6.4)
│   │   └── logging_filter.py       # PII-scrubbing log filter (§14)
│   ├── corpus/
│   │   ├── loader.py               # loads snapshots A + B, normalizes B, builds (scheme_id, section) → block
│   │   ├── quarantine.py           # sentences/fields never quoted (§5.1 data-quality rules)
│   │   ├── drift.py                # A-vs-B comparison report for overlapping facts
│   │   └── sentences.py            # deterministic sentence splitter + selector
│   ├── weekly/
│   │   ├── pipeline.py             # run_weekly(as_of) orchestration (§7)
│   │   ├── ingest.py
│   │   ├── cluster.py              # ≤5 themes, reviewIds, window
│   │   ├── trends.py               # weekly buckets → recurring / rising / one-off
│   │   ├── pulse.py                # pulse assembly + word cap
│   │   └── explainer.py            # fee explainer assembly + bullet cap
│   ├── response/
│   │   ├── graph.py                # LangGraph build (§8.2)
│   │   ├── state.py                # TicketState TypedDict + reducers
│   │   ├── agents/
│   │   │   ├── facts.py            # model → (scheme_id, section) | none ; code → answer
│   │   │   ├── pattern.py          # model → theme_id ; code → trend, count, window
│   │   │   └── compliance.py       # model → advice | performance_claim | none ; code → flags
│   │   ├── orchestrator.py         # merge rules, templates, canonical reuse (§8.4)
│   │   ├── reviewer.py             # hard-fail checks (§8.5)
│   │   ├── refinement.py           # pushback → node routing, cap 5 (§8.7)
│   │   └── templates.py            # refusal / not-covered / footer templates
│   ├── bridge/
│   │   ├── repeats.py              # question fingerprint + 7-day counts (§9.1)
│   │   └── canonical.py            # canonical explainer registry (§9.2)
│   ├── gate/
│   │   ├── actions.py              # ProposedAction lifecycle (§10)
│   │   ├── executor.py             # executes exactly one approved action
│   │   └── mcp_client.py           # MCP stdio client session (Python mcp SDK) → spawns node mcp-server/dist/index.js
│   ├── llm/
│   │   ├── client.py               # LLMClient interface, closed-list JSON enforcement
│   │   └── prompts/                # one prompt file per agent
│   ├── scheduler.py                # APScheduler cron + lock
│   └── api/
│       ├── queue.py  weekly.py  sources.py  approvals.py  explainers.py  health.py
├── mcp-server/                     # TypeScript, vendored from Multi-Agentic-System/mcp-server (D1)
│   ├── package.json                # reply-desk-mcp; Node ≥ 22; build → dist/
│   ├── src/index.ts                # StdioServerTransport entrypoint
│   ├── src/mcp/tools/              # googleDocsAppend.ts, gmailDraft.ts (no send tool)
│   ├── src/schemas/                # zod input schemas (+ idempotency_key, §10.4)
│   ├── src/services/               # googleDocsService.ts, gmailService.ts, googleAuthService.ts (M3)
│   │   └── localFileService.ts     # NEW: MCP_BACKEND=local → samples/
│   ├── src/scripts/auth.ts         # one-time Google OAuth (M3)
│   └── tests/                      # vitest (M3) + local backend + idempotency tests
├── ui/                             # React + Vite SPA (§13)
├── evals/
│   ├── run_all.py                  # entrypoint: python -m evals.run_all
│   ├── feasibility.py              # §8.1
│   ├── edit_correctness.py         # §8.2
│   ├── grounding_compliance.py     # §8.3
│   ├── fixtures/                   # adversarial + out-of-scope question sets
│   └── results/                    # committed JSON + Markdown reports (§9.1)
├── samples/                        # §9.1 artifacts
│   ├── reviews_sample.csv
│   ├── five_questions_with_drafts.md
│   ├── appended_doc_entry.md
│   └── email_draft.md
├── tests/                          # unit tests (guardrails, agents, gate)
└── docs/
    ├── problemStatement.md
    └── architecture.md             # this file
```

---

## 5. Data layer

### 5.1 Read-only inputs

#### Facts corpus: two snapshots

The corpus is built from **two frozen snapshots**. Snapshot B was copied from `RAG-Chatbot/backend/data/` **in addition to** snapshot A, so neither replaces the other.

| Snapshot | Path | Format | Captured ("last checked") | NAV date | Schemes |
| --- | --- | --- | --- | --- | --- |
| **A** | `data/parsed/*.json` + `parse_manifest.json` | `{scheme_id, source_url, nav_date, fetched_at, text_blocks:[{section_title, text}]}`, 7 blocks each | 2026-06-28 | 25-Jun-2026 | `large_cap`, `mid_cap`, `small_cap`, `gold_fof`, `silver_fof` |
| **B** | `data/processed/hdfc_*.json` | `{scheme_name, source_url, scrape_date, sections:[{section_name, content, data_points}]}`, 4 sections each (Gold FoF: 3) | 2026-09-01 | 31-Aug-2026 | `large_cap`, `mid_cap`, `small_cap`, `gold_fof`, `elss_tax_saver` |

Together they cover **6 schemes**. Four appear in both snapshots, `silver_fof` is only in A, and `elss_tax_saver` is only in B.

Both snapshots are loaded once at startup into one immutable `Corpus` object:

```python
Corpus.blocks: dict[(scheme_id, section), Block]          # resolved view used by agents
Corpus.all_blocks: dict[(scheme_id, section, snapshot), Block]   # both snapshots, for Sources panel + drift
Block = {scheme_id, scheme_name, section, text, source_url, snapshot: "A"|"B",
         last_checked, nav_date, origin: {file, section_title|section_name}}
```

**Scheme id mapping for B** (from file name): `hdfc_large_cap_fund` → `large_cap`, `hdfc_mid_cap_fund` → `mid_cap`, `hdfc_small_cap_fund` → `small_cap`, `hdfc_gold_etf_fof` → `gold_fof`, `hdfc_elss_tax_saver_fund` → `elss_tax_saver`.

| `scheme_id` | Exit load (verbatim, resolved snapshot) | `source_url` | Snapshots |
| --- | --- | --- | --- |
| `large_cap` | "Exit load of 1% if redeemed within 1 year" | https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth | A, B |
| `mid_cap` | "Exit load of 1% if redeemed within 1 year." | https://groww.in/mutual-funds/hdfc-mid-cap-fund-direct-growth | A, B |
| `small_cap` | "Exit load of 1% if redeemed within 1 year" | https://groww.in/mutual-funds/hdfc-small-cap-fund-direct-growth | A, B |
| `gold_fof` | "Exit load of 1%, if redeemed within 15 days." | https://groww.in/mutual-funds/hdfc-gold-etf-fund-of-fund-direct-plan-growth | A, B |
| `silver_fof` | "Exit load of 1%, if redeemed within 15 days." | https://groww.in/mutual-funds/hdfc-silver-etf-fof-direct-growth | A |
| `elss_tax_saver` | "Nil" | https://groww.in/mutual-funds/hdfc-elss-tax-saver-fund-direct-plan-growth | B |

**Canonical section list.** The Facts agent picks from one closed list of 7 sections. The loader maps each snapshot's native sections onto it:

| Canonical section | Snapshot A block | Snapshot B source (after normalization) |
| --- | --- | --- |
| `Key fund facts` | Key fund facts | Fund Details (fund size, expense ratio) + NAV & AUM (NAV, date) + Fund Overview riskometer sentence |
| `Exit load` | Exit load | Tax Info & Exit Load → the line after `Exit load` |
| `Stamp duty and tax` | Stamp duty and tax | Tax Info & Exit Load → `Stamp duty on investment` + `Tax implication` lines |
| `Minimum investments` | Minimum investments | Fund Details `Min. for SIP` + Fund Overview "Minimum SIP / Lumpsum Investment" sentences |
| `Fund management` | Fund management | Fund Overview "… is the Current Fund Manager …" sentence |
| `Benchmark` | Benchmark | — (A only) |
| `Investment objective` | Investment objective | — (A only) |

So for `elss_tax_saver` (B only), `Benchmark` and `Investment objective` are **not covered**, while all 7 sections are available for `silver_fof` (A only).

**Normalizing snapshot B.** These deterministic, unit-tested transforms run at load time. The normalized text is what agents quote and what the Reviewer's substring check runs against. The Sources panel shows it next to the original `content`.

1. Split each B section into canonical sub-blocks using the label lines shown above.
2. Join `label\nvalue` pairs as `label: value.`. For example, `Expense ratio\n1.02%` becomes `Expense ratio: 1.02%.`
3. Collapse whitespace and strip trailing `;` artifacts.

**Data-quality quarantine** (`corpus/quarantine.py`). The following items in B are **never quoted**, because they are scraper artifacts or read as judgements:

| Item | Why |
| --- | --- |
| "This scheme was made available to investors on 10 Dec 1999." | The same date appears for every scheme, which points to a template or scraper artifact. |
| Fund Overview sentence containing "Asset Under Management(AUM) of ₹9,67,910 Cr" | The same figure appears for every scheme (AMC-wide) and contradicts each scheme's `Fund size (AUM)`. NAV is taken from `NAV & AUM` instead. |
| Fund Details `Rating` (e.g. `Rating\n4`) | A star rating reads as a quality judgement, which is advice-adjacent. |
| `data_points` field | Values are malformed (e.g. `exit_load: ", stamp duty and tax"`), so the field is ignored entirely and only `content` is parsed. |

**Resolution policy (when both snapshots have a fact):**

- For each `(scheme_id, canonical section)`, `Corpus.blocks` resolves to **snapshot B if it has that section, otherwise A**. The newer snapshot wins.
- The chosen block's own `last_checked` and `nav_date` travel with it:
  - Snapshot B: `Last checked: 01-Sep-2026`, NAV date 31-Aug-2026.
  - Snapshot A: `Last checked: 28-Jun-2026`, NAV date 25-Jun-2026.
- A reply never mixes snapshots within one claim.

**Drift report** (`corpus/drift.py`). At startup, every overlapping `(scheme, section)` is compared across A and B. Differences go into a `corpus_drift` report that is shown in the Sources panel and logged. Drift is informational, not an error. Current drift:

| Scheme | Expense ratio A → B | Fund size A → B | Riskometer A → B | Exit load |
| --- | --- | --- | --- | --- |
| `large_cap` | 1.04% → 1.02% | ₹37,808.31 Cr → ₹40,197.89 Cr | Moderately High → Very High | unchanged |
| `mid_cap` | 0.75% → 0.74% | ₹97,350.48 Cr → ₹1,05,142.69 Cr | Moderately High → Very High | unchanged |
| `small_cap` | 0.77% → 0.75% | ₹38,809.48 Cr → ₹41,679.00 Cr | Moderately High → Very High | unchanged |
| `gold_fof` | 0.2% → 0.20% | ₹12,121.18 Cr → ₹11,197.05 Cr | (not in B) | unchanged |

Exit loads are identical across snapshots, so fee answers don't depend on which snapshot resolves.

**Supporting Milestone 1 artifacts** (copied with B, not on the runtime path):

- `data/raw/*.html`: the source HTML for B, captured on 2026-08-30 and 2026-09-01. It's kept for provenance and audit, and is never parsed at runtime.
- `data/chroma_db/`: the Milestone 1 Chroma index. Collection `mf_facts`, 23 chunks of B, `models/gemini-embedding-001`, chunk size 500 with overlap 50.
  - The Facts agent **doesn't** query it, because embedding the question would be a second model call in that node (§7 "one model call per agent").
  - It is used optionally by the eval harness as a cross-check that retrieval's top hit agrees with the Facts agent's `(scheme_id, section)` pick (§15.3).

**Startup validation:** the loader asserts all of the following, and the app refuses to start otherwise:

- A: `scheme_count == 5`, 7 blocks per scheme.
- B: 5 files present, and every `scheme_name` maps to a known `scheme_id`.
- The union is exactly 6 `scheme_id`s.
- Every `source_url` is on `groww.in`, and a scheme's URL is identical across A and B.
- Every resolved `(scheme_id, section)` is unique.
- No quarantined sentence survives normalization.

#### Reviews: `data/normalized_reviews.json`

- 1,650 records.
- Fields: `id`, `source`, `rating`, `title`, `text`, `date`, `version`.
- Window: 2026-04-12 → 2026-07-04.

The weekly loop also accepts a CSV with the same columns (`samples/reviews_sample.csv`). Both formats are parsed into the same `Review` model.

#### Baseline themes: `data/analyzed_themes.json`

Used as the **taxonomy seed** (theme ids, names and descriptions) and as a **regression baseline**. When the weekly loop runs on the unchanged review file, it must reproduce these counts within tolerance (§15.1).

### 5.2 Domain model

```mermaid
erDiagram
  WEEKLY_RUN ||--o{ THEME_SNAPSHOT : produces
  WEEKLY_RUN ||--o| PULSE : produces
  WEEKLY_RUN ||--o| FEE_EXPLAINER : produces
  FEE_EXPLAINER ||--o| CANONICAL_EXPLAINER : "promoted on approval"
  TICKET ||--o{ DRAFT_VERSION : has
  TICKET ||--o{ AGENT_OUTPUT : has
  TICKET ||--o{ REFINEMENT : has
  TICKET }o--|| QUESTION_CLUSTER : "fingerprint"
  QUESTION_CLUSTER ||--o{ PULSE : "repeat questions (≥5/7d)"
  PROPOSED_ACTION }o--|| PULSE : "doc append / email draft"
  PROPOSED_ACTION }o--|| FEE_EXPLAINER : "doc append / email draft"
  PROPOSED_ACTION }o--|| DRAFT_VERSION : "reply send"
  PROPOSED_ACTION ||--o{ AUDIT_EVENT : logs
```

### 5.3 Tables

All free-text columns hold **redacted** text only. A DB-level `CHECK` isn't practical for regex, so the repository layer runs `pii.scan()` on every write and raises `PIILeakError` if anything matches. Evals assert that this error never fires on real paths and that a final DB sweep is clean (§15.3).

| Table | Key columns | Notes |
| --- | --- | --- |
| `weekly_runs` | `id`, `as_of`, `trigger` (`cron`/`manual`), `status`, `started_at`, `finished_at`, `input_hash`, `error` | One row per run. `input_hash` = sha256 of the reviews file plus the corpus manifest. |
| `theme_snapshots` | `run_id`, `theme_id`, `name`, `description`, `review_ids` (JSON), `count`, `avg_rating`, `score`, `window_start`, `window_end`, `trend` (`recurring`/`rising`/`one_off`), `weekly_counts` (JSON), `is_top3`, `is_fee_theme` | ≤5 rows per run. |
| `pulses` | `id`, `run_id`, `body_md`, `word_count`, `quotes` (JSON: `[{review_id, text}]`), `action_ideas` (JSON ×3), `repeat_questions` (JSON), `status` | `word_count ≤ 250` is enforced before insert. |
| `fee_explainers` | `id`, `run_id`, `bullets` (JSON: `[{text, source_url, scheme_id, section}]`), `source_urls` (JSON ×2), `last_checked`, `status` | `len(bullets) ≤ 6` and `len(set(source_urls)) == 2` are enforced before insert. |
| `canonical_explainers` | `id`, `fee_explainer_id`, `theme_id='fees_brokerage'`, `covers` (JSON list of `(scheme_id, section)`), `approved_action_id`, `active`, `activated_at`, `superseded_at` | At most one `active` row per theme. |
| `tickets` | `id`, `received_at`, `channel`, `question_redacted`, `pii_types_found` (JSON list of type names only), `fingerprint`, `status` (`processing`/`awaiting_human`/`blocked`/`approved`/`rejected`/`sent`), `priority`, `route_team`, `rework_count`, `refinement_count` | Raw text is never stored. Only the *types* of PII found are kept, e.g. `["PAN","PHONE"]`. |
| `agent_outputs` | `ticket_id`, `agent` (`facts`/`pattern`/`compliance`), `attempt`, `model_raw_choice`, `validated_choice`, `written_output` (JSON), `latency_ms`, `created_at` | One row per agent per (re)run. Gives the structured-output evidence §8.1 needs. |
| `draft_versions` | `id`, `ticket_id`, `version`, `reply_text`, `citation_url`, `last_checked`, `routing_note` (JSON), `disagreement` (nullable text), `reviewer_result` (JSON), `origin` (`orchestrator`/`rework`/`refinement`/`human_edit`), `is_current` | Versions are append-only. An edit creates a new version for that ticket only. |
| `refinements` | `id`, `ticket_id`, `n` (1..5), `kind` (`shorter`/`different_scheme`/`check_fee_issue`/`other`), `hint_redacted`, `nodes_rerun` (JSON), `created_at` | Capped at 5 per ticket. |
| `question_clusters` | `fingerprint`, `representative_text`, `section`, `scheme_id`, `theme_id`, `first_seen`, `last_seen` | Counts are computed over `tickets` by time window, never stored as a running total. |
| `proposed_actions` | `id`, `kind` (`doc_append`/`email_draft`/`reply_send`), `subject_type`, `subject_id`, `target` (JSON), `payload` (exact rendered content), `payload_sha256`, `status` (`pending`/`approved`/`rejected`/`executed`/`failed`/`superseded`), `decided_by`, `decided_at`, `executed_at`, `result` (JSON) | §10. |
| `audit_events` | `id`, `ts`, `actor`, `event`, `entity_type`, `entity_id`, `details` (JSON, redacted) | Append-only. Covers every approve, reject, edit, refinement, execution and run. |
| `outbox_replies` | `id`, `ticket_id`, `draft_version_id`, `reply_text`, `sent_at` | Target of `reply_send` (§10.4). |

---

## 6. Scope enforcement (guardrails)

Everything in this section is plain Python with unit tests. Prompts repeat the rules, but **code is the source of truth** (§5.4, §7).

### 6.1 PII redaction: `app/guardrails/pii.py`

**Where it runs:**

| Layer | Purpose |
| --- | --- |
| **Ingress** | `POST /api/tickets`, review ingest, and refinement hints. The text is redacted **before** the Pydantic model is built, so raw text never reaches the DB, the graph state, the LLM or the logger. |
| **Reviewer** | Re-scans the final draft and treats any match as a hard fail. |
| **Log filter** | Re-redacts every log record's message and args (defense in depth, §14). |
| **Repository writes** | `scan()` on all text columns. A hit raises an error. |
| **MCP executor** | `scan()` on the payload right before the tool call. A hit marks the action `failed`. |

**Pre-normalization** (edge-case G5). `redact()` first builds a **scan view** of the text. All patterns run on this view, and the redaction is applied to it. Unicode tricks therefore can't hide PII.

1. Apply Unicode **NFKC**, which folds full-width digits and letters.
2. Strip zero-width characters (`U+200B–U+200D`, `U+2060`, `U+FEFF`).
3. Map non-ASCII decimal digits, e.g. Devanagari `०–९`, to `0–9`.
4. Collapse runs of whitespace.

The stored and returned text is the redacted scan view, never the original. Cyrillic or Greek homoglyphs, e.g. `А` in a PAN, aren't folded by NFKC. A small A–Z confusables map handles the common ones, and anything else is a known limit (§17.2).

**Compact window for PAN.** After the main pass, a second pass removes spaces and hyphens inside any 10–14-character window shaped like `[A-Z]{5}[\s-]*\d{4}[\s-]*[A-Z]`, then re-tests the PAN pattern. This catches `ABCDE 1234 F` and a PAN split across a line break.

**Patterns** are applied in order, so that longer or more specific numbers are captured before the generic account-number rule:

| Type | Pattern (simplified) | Token |
| --- | --- | --- |
| EMAIL | `[\w.+-]+@[\w-]+\.[\w.-]+` | `[EMAIL]` |
| PAN | `\b[A-Z]{5}[0-9]{4}[A-Z]\b` (case-insensitive, then upper-cased check) | `[PAN]` |
| AADHAAR | `\b[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}\b` (+ optional Verhoeff check to cut false positives) | `[AADHAAR]` |
| PHONE | `(?:\+?91[\s-]?\|0)?[6-9]\d{4}[\s-]?\d{5}\b`, **plus a context rule** (G13): any 10-digit run (spaces or hyphens allowed) within 20 chars of `call\|mobile\|phone\|whatsapp\|contact\|number\|no\.` regardless of its first digit | `[PHONE]` |
| OTP | `\b(?:otp\|one[- ]time (?:password\|code)\|verification code\|pin)\b\W{0,10}\d{4,8}\b` | `[OTP]` |
| ACCOUNT | `\b\d{9,18}\b` when within 30 chars of `a/c\|acct\|account\|folio\|ac no\|bank`, **or** any bare digit run of 11–18 | `[ACCOUNT]` |
| UPI ID | `\b[\w.-]+@(?:ok\w+\|ybl\|paytm\|upi\|ibl\|axl)\b` | `[UPI]` |
| IFSC (context) | `\b[A-Z]{4}0[A-Z0-9]{6}\b` | `[IFSC]` |

**API:**

```python
def redact(text: str) -> RedactionResult:  # .text (redacted), .types (sorted set of type names)
def scan(text: str) -> list[str]            # returns PII types found; [] means clean
```

The `RedactionResult` holds no reference to the original string. The caller's variable is dropped after `redact()` returns. Tokens such as `[PAN]` are on the allow-list, so a second `scan()` treats them as clean.

When anything is redacted, the Compliance agent's code sets a **PII flag** (`pii_detected=True`, `pii_types=[...]`) (§5.2).

### 6.2 Scope validators: `app/guardrails/scope.py`

Each validator returns `list[Violation(code, span)]`.

| Validator | Code | Examples matched |
| --- | --- | --- |
| `check_advice` | `ADVICE` | `\b(you (should\|may want to\|might want to\|could consider)\|we recommend\|i('d\| would) (suggest\|recommend)\|consider (staying\|holding\|switching\|investing)\|better (fund\|option\|choice)\|good (fund\|investment\|time to))\b`, "stay invested", "best fund" |
| `check_buy_sell` | `BUY_SELL` | `\b(buy\|sell\|redeem now\|exit now\|switch to\|book profit\|hold on to\|accumulate)\b` in imperative or second-person context |
| `check_performance` | `PERFORMANCE` | `\b\d+(\.\d+)?\s?%\s*(returns?\|cagr\|xirr\|p\.?a\.?\|annuali[sz]ed\|gain)\b`, `\b(returned\|delivered\|generated\|outperform\w*\|beat the (market\|benchmark))\b`, `\b(1\|3\|5\|10)[- ]?(y\|yr\|year)s? returns?\b`, "expected return", "will grow" |

**Corpus-span exemption.** Corpus text can legitimately contain words like "returns" and percentages, e.g. *"returns are taxed at 20%"* or *"Exit load of 1%"*. Validators therefore run on the **generated** portions of a draft. Any span that is a verbatim substring of a corpus block or an approved explainer bullet is marked `provenance=corpus` and skipped. This keeps factual fee and tax statements while blocking any generated return claim.

**Other exempt provenances** (edge-case G3). Two more kinds of span skip the advice, buy/sell and performance validators. PII checks **always** apply to them.

| Provenance | What it covers | Why exempt |
| --- | --- | --- |
| `template` | Sentences matched **exactly** against `response/templates.py` (refusals, not-covered, lead-ins, disagreement text) | The advice refusal must say *"…whether to buy, hold or redeem"*. Without this, `check_buy_sell` would fail every refusal on its own words. Templates are reviewed once, in code. |
| `question` | Representative user questions quoted in the pulse's repeat-questions section (§9.1), rendered in quotation marks with their count | These are the users' own words reported to Product, not our advice. Example: *"should I redeem?"* asked 6×. |

Pulse **review quotes** aren't exempt. Reviews containing advice, buy/sell or performance language are filtered out before selection instead (§7.6).

### 6.3 Allow-lists: `app/guardrails/allowlists.py`

| List | Values | Used by |
| --- | --- | --- |
| `SCHEME_IDS` | 6 ids: union of snapshot A (`parse_manifest.json`) and B (`processed/` mapping) | Facts agent output validation |
| `SECTIONS` | 7 canonical section titles (§5.1) | Facts agent output validation |
| `THEME_IDS` | ids from the current run's `allThemes` (≤5) | Pattern agent output validation |
| `COMPLIANCE_FLAGS` | `advice`, `performance_claim`, `none` | Compliance agent output validation |
| `CITATION_URLS` | 6 scheme `source_url`s | Reviewer: every factual claim's link must be here |
| `EDU_URLS` | `https://www.amfiindia.com/investor-corner`, `https://investor.sebi.gov.in/` | Refusal templates only |
| `PUBLIC_DOMAINS` | `groww.in`, `hdfcfund.com`, `amfiindia.com`, `sebi.gov.in` | Rejects any URL outside this list anywhere in outputs (§7 "public sources only") |

### 6.4 Length guards: `app/guardrails/length.py`

- `count_sentences(text)` is a deterministic splitter that handles `Rs.`, `e.g.`, `p.a.`, `₹1,000.50` and decimals in numbers. The citation line and the "Last checked" footer are **structured fields**, not part of `reply_text`, so they aren't counted.
- `count_words(md)` strips Markdown syntax and counts whitespace-delimited tokens, with review-id tags excluded. The pulse cap is 250.
- `count_bullets(list)` enforces the explainer cap of 6.

Every writer **enforces** its cap by construction (§7.6, §8.4). It doesn't generate freely and then hope the output fits. The validators run again in the Reviewer and the evals.

---

## 7. Weekly loop

### 7.1 Trigger and run envelope

```mermaid
sequenceDiagram
  autonumber
  participant S as Scheduler / Manual trigger
  participant P as weekly.pipeline
  participant DB as Store
  participant B as bridge.repeats
  participant G as gate.actions

  S->>P: run_weekly(as_of, trigger)
  P->>DB: acquire run lock, insert weekly_runs(status=running)
  P->>P: ingest + redact reviews
  P->>P: cluster → ≤5 themes (reviewIds, count, window)
  P->>P: trends per theme (weekly buckets)
  P->>P: select top 3 (excl. other) + fee theme (fees_brokerage)
  P->>B: repeat_questions(window=7d ending now, min=5)
  B-->>P: [{representative_text, count, window, fingerprint}]
  P->>P: build pulse (≤250 words) + validate
  P->>P: build fee explainer (≤6 bullets, 2 links) + validate
  P->>DB: insert themes, pulse, explainer
  P->>G: propose doc_append(pulse), email_draft(pulse), doc_append(explainer), email_draft(explainer)
  P->>DB: weekly_runs(status=awaiting_approval)
```

- **`as_of`** defaults to `min(now_ist, max(review.date))`. The review dataset is static and ends on 2026-07-04, so "this week" means the last 7 days of data (2026-06-28 → 2026-07-04). Otherwise "rising" would always be computed against an empty current week. When live reviews are added, `as_of` naturally becomes "now".
- **Two clocks** (edge-case G1):
  - **Review trends** (themes, counts, `rising`/`recurring`) use **data time** (`as_of`).
  - **Repeat questions** from the response loop use **ticket time** (`now`, a rolling 7 days).
  - With the static review set these differ by months. Anchoring repeat questions to `as_of` would therefore always find zero tickets.
  - The pulse labels both windows explicitly (§7.6).
- **Idempotency.** If a run with the same `input_hash` and `as_of` already finished, a cron trigger is a no-op. A manual trigger creates a new run, and its proposed actions supersede the old run's *pending* actions. Approved or executed actions are never touched.
- **No manual intervention.** Every step is deterministic or has a deterministic fallback. For example, if the LLM call for action ideas fails, templated ideas per theme are used. The run always ends in `awaiting_approval` or `failed` with an error. It never stalls on input.

### 7.2 Ingest

1. Parse JSON or CSV into `Review` objects and drop rows without `id`, `text` or `date`. `version` is optional, because 227 of the 1,650 reviews have no `version` key at all. Read it with `.get()` and never mark it required. Rows with a `rating` outside 1–5 or an unparseable `date` are dropped and counted in the run record.
2. Run `pii.redact()` on `title` and `text`. Reviews are public, but users do paste phone numbers and emails into them.
3. De-duplicate by `id`.
4. Compute `window_start = min(date)` and `window_end = max(date)` (date part, IST).

### 7.3 Clustering (≤5 themes)

- **Taxonomy.** The Milestone 2 theme set is fixed: `trading_charts`, `money_movement`, `reliability`, `fees_brokerage`, `other`. A fixed taxonomy keeps theme ids stable across weeks, which the Pattern agent's closed list and week-over-week trends both need. It also guarantees the **max-five** cap.
- **Assignment.** Each review gets exactly one theme. The method is the Milestone 2 keyword and phrase rules, scored per theme, with ties broken by a fixed priority order: `fees_brokerage` > `money_movement` > `reliability` > `trading_charts` > `other`. Fees takes priority so that "charged brokerage on a failed order" is counted as fee confusion.

  | Theme | Indicative lexicon |
  | --- | --- |
  | `fees_brokerage` | exit load, brokerage, charges, charged, fee, DP charge, stamp duty, hidden charge, deducted, commission, GST, AMC charge |
  | `money_movement` | withdraw, deposit, UPI, bank, refund, payment, mandate, settlement, credited |
  | `reliability` | crash, slow, lag, hang, not loading, error, bug, logout, OTP not received |
  | `trading_charts` | chart, order, F&O, option, intraday, indicator, candle, stop loss, GTT, trade |
  | `other` | fallback |

- **Per-theme output:** `id`, `name`, `description`, `reviewIds`, `count = len(reviewIds)`, `avgRating`, `score` (the Milestone 2 formula carried forward: count weighted by rating negativity), and `window` = `[window_start, window_end]`.
- **Regression check.** On the unchanged dataset, the counts must match `analyzed_themes.json` (709 / 503 / 162 / 140 / 136) within ±2%. Any drift is reported in the run record.

### 7.4 Trends (feeds Pattern agent and pulse)

For each theme, bucket `reviewIds` by ISO week (IST) across the window to get `weekly_counts`. Then:

| Label | Rule (evaluated in order) |
| --- | --- |
| `rising` | `last2 = mean(last 2 weeks)`, `prior = mean(earlier weeks)`. `last2 ≥ 1.5 × prior` **and** `sum(last 2 weeks) ≥ 5`. |
| `recurring` | The theme appears in ≥ 50% of weeks in the window **and** `count ≥ 20`. |
| `one_off` | Otherwise. |

All thresholds live in `config.py` and are shown in the UI tooltip next to the label.

**Partial weeks** (edge-case G12). The review window starts on a Sunday (2026-04-12) and ends on a Saturday (2026-07-04). The first ISO week therefore holds 1 day and the last holds 6, which would skew `last2` and `prior`.

- The trend math uses **per-active-day rates** (`weekly_count / days_with_data_in_week`) and excludes edge weeks with fewer than 4 days of data.
- Theme `count` and `window` still include every review, so provenance is unchanged.
- With fewer than 2 full prior weeks, `rising` can't be assigned (no division by an empty mean), and the rule falls through to `recurring`/`one_off`.

### 7.5 Top three and fee theme

- `topThemes` = the 3 highest `score` themes, excluding `other`. On the baseline data these are `trading_charts`, `money_movement` and `reliability`.
- **Fee theme** = `fees_brokerage`, always selected for the explainer **regardless of rank**. It sits 4th on the baseline (score 302.26 vs `reliability` at 302.36). The pulse always mentions it under a "Fee confusion" line, even when it isn't in the top 3.
- **What the fee theme actually contains** (edge-case G4). In the baseline data, **none** of the 136 `fees_brokerage` reviews mention "exit load". The most frequent terms are "charges" (71), "brokerage" (60), "charge" (40), "hidden" (14) and "fee/fees" (10). The review-side confusion is about **brokerage and hidden charges on trading**, which the scheme-page corpus doesn't cover. Exit-load confusion shows up on the **support side** (tickets).

  The system reports this as it is:

  - The pulse's fee line lists the theme's real top terms, computed from its `reviewIds`. It never uses a fixed "exit load" label.
  - The fee explainer is scoped to the scheme-level charges the corpus does hold (§7.7).
  - Brokerage and DP-charge questions in the response loop are "not covered" and routed to Support + Product (Pricing) (§8.5).
  - The README states the finding.

### 7.6 Pulse writer: `weekly/pulse.py`

**Structure** (Markdown):

```
# Weekly Product Pulse — week ending {as_of}  (reviews {window_start} → {window_end})
Facts-only. No investment advice.

## Top themes
1. {name} — {count} reviews, avg {avgRating}★, {trend} ({window})
   > "{quote}" — review {review_id}
2. …
3. …

## Fee confusion
{fees_brokerage count} reviews ({window}), {trend}. Most cited: {top fee terms from reviewIds}.
Scheme-level charges (exit load, stamp duty, expense ratio) → see fee explainer; brokerage is outside the scheme corpus.

## Repeat questions from support (tickets, {now−7d} → {now})   ← ticket clock, not review window
- "{representative_text}" — {count}× ({window})          ← from bridge §9.1

## Action ideas
1. …  2. …  3. …
```

**Quote selection.** Quotes are picked by code, one per top theme:

- Use reviews in the theme with rating ≤ 2, text length between 40 and 200 characters, and the highest lexicon-hit density.
- Exclude any review whose redaction found PII. This is stricter than redacting the quote.
- Exclude any review that fails `check_advice`, `check_buy_sell` or `check_performance` (edge-case G2). The baseline data has 95 reviews containing "buy" or "sell", 22 with a `%` figure and 21 with advice-like phrasing. Quotes are copied verbatim, so they're **skipped, never edited**.
- If no candidate is left, the length filter is relaxed first. The PII and scope filters are never relaxed. If still nothing qualifies, the theme shows "no quotable review this week".
- The quote is copied verbatim from the redacted text and tagged with its `review_id`.

**Action ideas.** This is one LLM call per run:

- **Input:** top-3 theme names and descriptions, their counts and trends, the fee keywords, and the repeat questions.
- **Output:** JSON `{"ideas": [str, str, str]}`.
- **Validation:** each idea must be ≤ 25 words, pass `check_advice`, `check_buy_sell`, `check_performance` and `pii.scan`, and contain no URL outside `PUBLIC_DOMAINS`.
- **Fallback:** any failure falls back to a fixed template per theme, e.g. *"Add an in-app exit-load explainer on the redemption screen linking the scheme page."*

These ideas are addressed to the product team, not to investors. The same scope validators still apply because the rules cover "both loops".

**Word-cap enforcement.** The writer assembles the pulse and counts words. If the count is over 250, it trims in this order:

1. Shorten quotes to ≤ 20 words, with an ellipsis.
2. Drop the third quote.
3. Keep only the top 3 repeat questions, with a "+N more" link to the UI.
4. Compress the action ideas to their first clause.

The writer asserts `≤ 250` before insert.

### 7.7 Fee explainer writer: `weekly/explainer.py`

**Rule:** bullets are **verbatim corpus sentences**. Code selects them and the model writes nothing.

- **Two source links.** One equity-family scheme and one commodity-FoF scheme, because their exit loads differ (1 year vs 15 days). The defaults are configurable: `large_cap` and `gold_fof`.
- **Bullet plan (≤ 6)**. Each bullet is copied from the **resolved** block (§5.1). Both default schemes resolve to snapshot B. Each bullet is tagged with its `source_url` and snapshot:

| # | Source block (resolved) | Content (verbatim, normalized) |
| --- | --- | --- |
| 1 | `large_cap` / Exit load (B) | "Exit load of 1% if redeemed within 1 year" |
| 2 | `gold_fof` / Exit load (B) | "Exit load of 1%, if redeemed within 15 days." |
| 3 | `large_cap` / Stamp duty and tax (B) | "Stamp duty on investment: 0.005% (from July 1st, 2020)." |
| 4 | `large_cap` / Key fund facts (B) | "Expense ratio: 1.02%." |
| 5 | `gold_fof` / Key fund facts (B) | "Expense ratio: 0.20%." |
| 6 | `large_cap` / Stamp duty and tax (B) | "If you redeem within one year, returns are taxed at 20%." |

- **Header** (not a bullet): the scheme names covered and the scope line *"Scheme-level charges from the official scheme pages. Brokerage and trading charges are not covered here."* (edge-case G4).
- **Bullet ordering:** fee-keyword frequency in the theme's reviews is matched against bullet topics (exit load, stamp duty, expense ratio, tax). Terms with no corpus bullet, such as "brokerage" or "hidden charges" (the most common today), don't affect ordering. With no matches, the default order in the table above is used.
- **Footer:** `Sources: {url1}, {url2} · Last checked: 01-Sep-2026`. If bullets resolve to different snapshots, the footer shows each bullet's own date.
- **ELSS is not in the default explainer.** Its exit load is "Nil" (snapshot B), which is itself a common point of confusion. Swapping a default scheme for `elss_tax_saver` is a config change (`EXPLAINER_SCHEMES`), but the two-link cap still applies.
- **Why these bullets:** they answer the confusion that `fees_brokerage` reviews raise ("why was I charged", "what is this deduction"). The bullet order is driven by fee-keyword frequency in the theme's reviews, so if "stamp duty" outranks "expense ratio", it moves up.
- **Validation:**
  - `count_bullets ≤ 6`
  - exactly 2 distinct URLs, both in `CITATION_URLS`
  - every bullet is a substring of its cited block
  - `last_checked` present

### 7.8 Proposed actions from the weekly loop

Each run proposes **four independent actions**: pulse doc-append, pulse email-draft, explainer doc-append and explainer email-draft. Each is approved or rejected on its own (§10).

Approving the **explainer doc-append** has one extra effect: it promotes that explainer to `canonical_explainers` (§9.2). This is the only cross-loop side effect of an approval, and it's documented on the approval screen *before* the approval is given. It is not a second write action, so it doesn't break "approving one action does not trigger another" (§15.2 tests this explicitly).

---

## 8. Response loop

### 8.1 Ticket ingestion

```
POST /api/tickets {text, channel}
  → pii.redact(text)                       # raw text discarded here
  → tickets.insert(question_redacted, pii_types_found, status=processing)
  → graph.ainvoke({ticket_id, question: redacted, pii_types}, thread_id=ticket_id)
```

Seed questions (`data/seed_questions.json`) are loaded through the same endpoint, so evals exercise the real ingress path.

### 8.2 LangGraph topology

```mermaid
flowchart TD
  START((START)) --> entry{entry_router}
  entry -->|new ticket| guard[redaction_guard<br/>assert scan(question)==∅]
  guard --> facts[facts_agent]
  guard --> pattern[pattern_agent]
  guard --> compliance[compliance_agent]
  facts --> orch[orchestrator]
  pattern --> orch
  compliance --> orch
  orch --> rev[reviewer]
  rev -->|pass| done((END: awaiting_human))
  rev -->|fail & rework_count==0| rework[mark_rework<br/>rework_count=1, failures→state]
  rework --> orch
  rev -->|fail & rework_count==1| blocked((END: blocked, send not offered))

  entry -->|refine: shorter| orch
  entry -->|refine: different_scheme| facts_only[facts_agent]
  entry -->|refine: check_fee_issue| pattern_only[pattern_agent]
  facts_only --> orch
  pattern_only --> orch
```

- **Parallelism.** `redaction_guard` has three outgoing edges, so LangGraph runs `facts`, `pattern` and `compliance` in the same superstep. Each node **reads only** `question`, `pii_types` and static resources (corpus, themes, bridge counts). Each **writes only** its own state key, so no node reads another agent's output. The orchestrator is the fan-in point: it runs after all three branches finish.
- **Refinement entry.** On a refinement, `entry_router` routes straight to the affected node. Unaffected agent outputs persist in the checkpointed state and are reused as they are (§8.7).

### 8.3 State schema: `response/state.py`

```python
class FactsOut(TypedDict):
    scheme_id: str | None
    section: str | None
    covered: bool
    answer_sentences: list[str]        # verbatim corpus sentences
    citation_url: str | None
    snapshot: Literal["A", "B"] | None # which corpus snapshot the answer was copied from
    last_checked: str | None           # "01-Sep-2026" (B) or "28-Jun-2026" (A)
    searched: dict                     # {"schemes":[...6], "sections":[...7], "snapshots":["A","B"]} when not covered

class PatternOut(TypedDict):
    theme_id: str
    trend: Literal["recurring", "rising", "one_off"]
    review_count: int
    window: tuple[str, str]
    review_ids_sample: list[str]       # ≤5, for provenance in UI
    in_week_repeats: int               # from bridge, 7 days ending now
    repeat_window: tuple[str, str]

class ComplianceOut(TypedDict):
    advice: bool
    performance_claim: bool
    pii_detected: bool
    pii_types: list[str]

class TicketState(TypedDict):
    ticket_id: str
    question: str                      # REDACTED ONLY
    pii_types: list[str]
    refine_kind: str | None
    refine_hint: str | None            # redacted
    facts: FactsOut | None
    pattern: PatternOut | None
    compliance: ComplianceOut | None
    draft: Draft | None
    reviewer: ReviewerResult | None
    rework_count: int                  # 0 or 1
    refinement_count: int              # 0..5
    failures: list[str]                # reviewer failure codes fed to rework
```

Each agent key uses a "last write wins" reducer. No list-append reducers are shared across parallel nodes, which avoids `InvalidUpdateError` on concurrent writes.

### 8.4 The three agents

Every agent node follows the same pattern:

```python
choice = llm.choose(prompt, schema=Enum[...], timeout=8s)   # exactly ONE model call
choice = validate_against_allowlist(choice) or NONE          # invalid → NONE, never guessed
output = deterministic_writer(choice, corpus/themes/bridge)  # code writes everything
persist(agent_outputs, model_raw_choice, validated_choice, output)
```

`llm.choose` uses the provider's JSON or structured-output mode with a schema whose fields are **enums**. Malformed JSON gets **no retry**, because the rule is one model call per node. It maps to the null choice, which the writer handles. Example: Facts → "not covered".

#### Facts agent

- **Model sees:** the redacted question, plus the list of 6 `(scheme_id, scheme_name)` pairs and the 7 canonical section titles. It does **not** see corpus text, which keeps the call small and stops the model from paraphrasing facts.
- **Model returns:** `{"scheme_id": <enum|null>, "section": <enum|null>}`.
- **Code writes:**
  - **Both picks present, and the resolved block exists** (§5.1: B first, then A):
    1. Fetch the resolved block.
    2. Select up to 2 sentences. A "Key fund facts" block is split on `. ` into fact sentences, and the field whose label matches question keywords is chosen (e.g. "expense ratio" → `Expense ratio: 1.02%.`). Other blocks use their first 1–2 sentences. Quarantined sentences are never candidates.
    3. Return the sentences, `citation_url = source_url`, the block's `snapshot`, and that snapshot's `last_checked`.
  - **Both picks present, but that scheme has no such section in either snapshot** (e.g. `elss_tax_saver` + `Benchmark`): `covered=False`. `searched` names the scheme, the section and the snapshots checked.
  - **Either pick missing**, or the question names an AMC or scheme outside the corpus (detected by code with an AMC and scheme-name lexicon such as "SBI", "Axis", "Nippon", "Flexi Cap"): `covered=False`, and `searched` lists all 6 scheme names, all 7 sections, and both snapshots.
  - **Out-of-section topics** (edge-case G14). A code lexicon of topics the corpus doesn't hold forces `covered=False` even when the model picks a plausible section. The lexicon covers:
    - lock-in
    - returns history / CAGR
    - portfolio / holdings / top stocks
    - brokerage / DP charge
    - SIP date / mandate
    - KYC
    - nominee

    Example: *"What's the ELSS lock-in?"* would otherwise be stretched to `Exit load: Nil.`, or answered from general knowledge (3 years). Neither is allowed, because the corpus has no lock-in fact.
  - **Several in-corpus schemes named** (edge-case G10). Example: *"expense ratio of large cap vs mid cap"*.
    - The code scheme-name lexicon detects every in-corpus scheme mentioned and records `multi_scheme: [ids]`.
    - The answer is given only for the model's picked scheme.
    - The draft never uses comparative wording ("lower", "better", "cheaper").
    - The routing note and UI suggest a "Different scheme" refinement for the others.
  - **Scheme ambiguity.** If the section is present but the scheme is null, *and* the resolved text is identical across a scheme family (e.g. exit load for the three equity schemes), the writer may answer for the family. It cites one scheme's link and names the family. Otherwise the result is "not covered: please specify which of the 6 schemes". ELSS is never in the equity exit-load family, because its exit load is "Nil".

#### Pattern agent

- **Model sees:** the redacted question and the current run's `allThemes` (id, name, description). That's ≤5 options.
- **Model returns:** `{"theme_id": <enum>}`. An invalid value maps to `other`.
- **Code writes:** from the latest `theme_snapshots` row, `trend`, `review_count`, `window`, and a sample of up to 5 `review_ids`. From the bridge (§9.1), `in_week_repeats` for this ticket's fingerprint and its 7-day window.

#### Compliance agent

- **Model sees:** the redacted question only.
- **Model returns:** `{"flag": "advice" | "performance_claim" | "none"}`.
- **Code writes:**
  - `advice` and `performance_claim` booleans. Each is **OR**ed with deterministic detectors run on the question: advice-seeking patterns such as "should I", "is it good", "which is better", "worth it", and return-seeking patterns such as "returns", "CAGR", "how much will I get". This way, a model miss can't downgrade a regex hit.
  - `pii_detected` and `pii_types`, copied from the ingress redaction result.

### 8.5 Orchestrator: `response/orchestrator.py` (pure code)

**Inputs:** `facts`, `pattern`, `compliance`, the canonical explainer registry, `failures` (on rework) and `refine_kind`.

**Decision table** (first matching row wins for the reply body; routing is computed independently):

| # | Condition | Reply draft (`reply_text`, ≤ 3 sentences) | Citation |
| --- | --- | --- | --- |
| 1 | `compliance.advice` | Advice refusal template: *"We can't offer investment advice or recommend whether to buy, hold or redeem. For general guidance on mutual funds, please see the AMFI investor education page."* If facts are covered, one corpus sentence is appended. | AMFI/SEBI edu URL (+ scheme URL if fact appended, as a separate cited line) |
| 2 | `compliance.performance_claim` | Performance refusal template: *"We can't share or project return figures. You can view the scheme's official details on its page."* | The scheme's `source_url` only (factsheet/scheme page), or the edu URL if no scheme was picked |
| 3 | Canonical explainer exists **and** `pattern.theme_id == fees_brokerage` **and** `facts.section ∈ explainer.covers_sections` **and** (`facts.scheme_id ∈ explainer.covers_schemes` or a same-family match) | The matching **explainer bullet(s)** verbatim, ≤ 2, plus *"This is our standard explanation of this charge."* | That bullet's `source_url`. `origin=canonical` is shown in the UI. |
| 4 | `facts.covered` | The Facts agent's verbatim sentence(s) (≤ 2), with an optional lead-in from a fixed list (e.g. *"Per the scheme page:"*). | `facts.citation_url` |
| 5 | not covered | *"This isn't covered by the scheme information we hold. We searched: {6 schemes} — sections: {7 sections} (scheme pages as of 28-Jun-2026 and 01-Sep-2026)."* | none (no factual claim) |

Every draft also carries `Last checked: {date}` as a structured field.

**Routing note** (internal, never sent):

| Field | Rule |
| --- | --- |
| `team` | PII found or a compliance flag → **Compliance**. `fees_brokerage` → **Support + Product (Pricing)**. `money_movement` → **Payments Ops**. `reliability` → **Engineering**. `trading_charts` → **Product (Trading)**. `other` → **Support**. Not covered → **Support (knowledge gap)**. |
| `priority` | **P1**: `trend == rising` or `in_week_repeats ≥ 5`. **P2**: `trend == recurring`. **P3**: `one_off`. A PII flag bumps the ticket up one level (min P2). |
| `why` | One templated line built from the signals, e.g. *"fees_brokerage: 136 reviews 12-Apr→04-Jul-2026, recurring; 7 similar questions in last 7 days of tickets (18-Nov→24-Nov-2026)."* The review window and the ticket window are always labelled separately (§7.1, two clocks). |
| `evidence` | `review_ids_sample`, fingerprint and repeat count |
| `multi_scheme` | Present when the question named several in-corpus schemes (G10): the list of ids, and which one was answered |

**Disagreement handling** (§5.2.3):

- **Trigger:** the reply path is a clean fact (rows 3 or 4) **and** (`in_week_repeats ≥ 5` or `trend == rising`).
- **Effect:** the orchestrator sets `disagreement`, e.g. *"Facts agent has a clean, cited answer, but Pattern shows 7 identical questions this week (rising). Draft provided; ticket should NOT be closed — escalated to Product (Pricing)."*
- The ticket gets `close_on_send=False` and the UI shows a banner. The "Approve & send" action then sends the reply but keeps the ticket open in the escalated team's view.

**Rework mode.** When `failures` is non-empty, the orchestrator applies deterministic fixes per failure code before regenerating:

| Failure code | Fix |
| --- | --- |
| `UNCITED_CLAIM` | Drop sentences that aren't corpus- or template-sourced. If none are left, use row 5. |
| `ADVICE` / `BUY_SELL` | Force row 1. |
| `PERFORMANCE` | Force row 2. |
| `PII` | Re-redact `reply_text`. |
| `LENGTH` | Keep the first N sentences. |

**Refinement "shorter".** Reduce to 1 sentence (the primary fact or refusal line) and keep the citation.

### 8.6 Reviewer: `response/reviewer.py` (pure code, no model)

Checks are run on `draft.reply_text` plus `citation_url`:

| Check | Hard fail? | Rule |
| --- | --- | --- |
| `UNCITED_CLAIM` | **Yes** | Each sentence must be either (a) a verbatim substring of a corpus block or canonical bullet, with `citation_url` equal to that block's `source_url`, or (b) an exact template sentence from `templates.py`. Any other sentence that contains a digit, `%`, `₹` or a fee keyword counts as an uncited factual claim. Factual drafts must have a `citation_url` in `CITATION_URLS`. |
| `ADVICE` / `BUY_SELL` | **Yes** | `scope.check_advice` and `check_buy_sell` on non-corpus spans. |
| `PERFORMANCE` | **Yes** | `scope.check_performance` on non-corpus spans. |
| `PII` | **Yes** | `pii.scan(reply_text) == []`. |
| `LENGTH` | Yes | `count_sentences ≤ 3`. |
| `URL_SCOPE` | Yes | Every URL's domain is in `PUBLIC_DOMAINS`. |
| `MISSING_LAST_CHECKED` | Soft (warn) | Factual drafts must carry `last_checked`. |

**Rework protocol (§5.2.4):**

- **First fail:** `rework_count = 1`, the named `failures` go back to the orchestrator, and the reviewer runs again.
- **Second fail:** `status = blocked`. The draft is stored with `reviewer_result.flags`. **No `reply_send` action is created**, so the UI can't offer Send. The human may still edit (which re-runs the reviewer on the edit) or reject.
- **Blocked, then a passing human edit** (edge-case G9). The blocked **version** never gets a send. If a human edit creates a **new** draft version that passes the reviewer, that new version gets its own `reply_send` action and the ticket returns to `awaiting_human`. The Approvals card states *"This reply replaces a draft that failed review"* and shows the original flags. A failing edit leaves the ticket `blocked`.

### 8.7 Human actions and refinement

| Human action | Effect (scoped to this ticket only) |
| --- | --- |
| **Approve** | Approves this ticket's single pending `reply_send` action, which the executor then writes to the outbox. Nothing else changes. |
| **Edit** | `pii.redact(edit)`, then a new `draft_versions` row (`origin=human_edit`), then the reviewer runs on it. A pass replaces the pending `reply_send` payload by superseding the old action and creating a new one. On a previously `blocked` ticket it creates the first `reply_send` (§8.6, G9). A fail makes the failing version **current** and flagged, **supersedes** any pending `reply_send` for the older version, and sets the ticket to `blocked`. This stops a reviewer from approving text the human has already changed (eval EDIT-04). An edit identical to the current version is a no-op. |
| **Reject** | Ticket `status=rejected` and its pending action `rejected`. No other ticket, action or explainer changes. |
| **Refine** | Allowed only while `refinement_count < 5`. Sends `{kind, hint}`, where the hint is redacted. |

**Refinement routing** uses a closed list chosen by UI buttons, plus a free-text hint:

| `kind` | Nodes re-run | Hint usage |
| --- | --- | --- |
| `shorter` | orchestrator → reviewer | — |
| `different_scheme` | facts → orchestrator → reviewer | The hint is appended to the Facts prompt as *"User clarification: {hint}"*. The model still returns only enum picks. |
| `check_fee_issue` | pattern → orchestrator → reviewer | The Pattern prompt receives *"Reviewer suggests checking against fees_brokerage"*. The code also reports last week's `fees_brokerage` snapshot alongside the current one. |
| `other` | orchestrator → reviewer | — |

Each refinement writes a `refinements` row recording `n`, `kind`, `nodes_rerun` and a timestamp, plus an `audit_events` entry. Refinements don't reset `rework_count` semantics: each refinement pass gets its own single rework allowance. When the cap is reached, the Refine button is disabled and the API returns `409 refinement_cap_reached`.

### 8.8 End-to-end example: the exit-load complaint

Input: *"Why was I charged an exit load? My PAN is ABCDE1234F, call me on 9876543210. Should I just redeem everything?"*

1. **Ingress redaction:** *"Why was I charged an exit load? My PAN is [PAN], call me on [PHONE]. Should I just redeem everything?"* with `pii_types=["PAN","PHONE"]`.
2. **Agents** (in parallel):
   - Facts → `(null, "Exit load")`
   - Pattern → `fees_brokerage`
   - Compliance → `advice`. The regex "should I" also hits.
3. **Orchestrator:**
   - Row 1 applies: an advice refusal, plus one corpus sentence for the equity family, *"Exit load of 1% if redeemed within 1 year"*, cited to the `large_cap` URL, and the AMFI edu link.
   - Routing: Compliance + Support/Product (Pricing), P1 (PII bump, plus repeats ≥5 if seeded).
   - Disagreement noted if the fee issue is rising.
4. **Reviewer:** passes. The corpus span is exempt and the refusal is a template.
5. **Human:** approves, and the reply goes to the outbox.

---

## 9. Connecting the loops

### 9.1 Repeat questions → weekly pulse

**Fingerprint.** Each ticket gets a deterministic key from the agents' validated picks plus a normalized text signature:

```
fingerprint = sha1(f"{pattern.theme_id}|{facts.section or 'uncovered'}|{compliance_class}|{simhash_bucket(normalized_text)}")
normalized_text = lowercase → strip PII tokens → strip stopwords/punctuation → lemmatize → sort unique tokens
compliance_class = advice | performance | factual
```

`simhash_bucket` uses a 64-bit simhash with the top 8 bits as the bucket. Rephrasings of the same question ("why exit load charged", "exit load deducted why?") then collide, while different questions within the same section stay apart. The scheme is deliberately **not** part of the key, because "exit load on Mid Cap" and "exit load on Small Cap" count as the same confusion.

**Counting.**

```python
repeat_count(fp, now) = COUNT(tickets WHERE fingerprint = fp AND received_at ∈ (now − 7d, now])
```

- **Clock** (edge-case G1). The window is anchored on **ticket time (`now`)**, never on the weekly loop's review `as_of`. The review data ends on 2026-07-04 while tickets arrive today, so an `as_of`-anchored window would always be empty. `received_at` is stored in UTC, and windows are computed and displayed in IST.
- The count is always computed on demand, never cached, so edits and rejections can't skew it. Rejected tickets still count, because the question was still asked.
- **Burst flag.** There is deliberately no user identity, so the same person repeating a question can't be de-duplicated. Instead, a cluster gets `possible_duplicate_burst=true` when ≥ 3 of its tickets have identical normalized text within 10 minutes. The pulse shows the flag beside the count.

**Effects:**

- **Pattern agent:** `in_week_repeats` comes from this count, which feeds priority (P1 when ≥5) and the disagreement rule.
- **Weekly loop:** `bridge.repeats.repeat_questions(now, min_count=5)` returns every fingerprint with ≥5 tickets in the 7-day ticket window as `{representative_text, count, window_start, window_end, theme_id, possible_duplicate_burst}`.
  - They're printed in the pulse's "Repeat questions from support" section **with count and window**, labelled as the ticket clock.
  - `representative_text` is the most frequent redacted wording in the cluster. It's rendered with `provenance=question` (§6.2), so a repeated *"should I redeem?"* can be reported to Product without failing the advice validator.
  - The top 3 clusters are shown, plus "+N more".
- **Demo/eval:** `seed_questions.json` includes 6 rephrasings of the exit-load question. Running them through the queue and then triggering the weekly loop shows them in the pulse. This is evidence for §5.3 row 1.

### 9.2 Approved fee explainer → canonical answer

```mermaid
sequenceDiagram
  autonumber
  participant W as Weekly loop
  participant G as Approval gate
  participant H as Human
  participant C as canonical registry
  participant O as Orchestrator

  W->>G: propose doc_append(fee explainer #E)
  H->>G: approve action A (doc_append #E)
  G->>G: execute A via MCP (doc append only)
  G->>C: promote #E → canonical (theme=fees_brokerage, covers=[(large_cap|equity family, Exit load), (gold_fof|FoF family, Exit load), …])
  C->>C: deactivate previous canonical, record superseded_at
  Note over O: next ticket
  O->>C: lookup(theme=fees_brokerage, section, scheme)
  C-->>O: bullet(s) + source_url
  O->>O: reuse bullet verbatim (row 3), origin=canonical
```

- **Promotion trigger.** Only the approval of the explainer's **doc_append** action promotes it. The email-draft approval doesn't, and neither does a rejected doc_append. In both cases the previous canonical explainer stays active.
- **Atomicity** (edge-case G8). Promotion runs in the **same DB transaction** that records the action as `executed`, after the MCP call has returned.
  - If promotion fails, e.g. on a DB error, the action is still recorded as `executed` with `result.promotion = "pending"`.
  - A startup and on-demand reconciler retries `promote(explainer_id, action_id)`, which is idempotent.
  - The MCP call is **never** re-executed to repair a failed promotion.
- **Ordering** (edge-case G8). An explainer is promoted only if its run's `as_of` is ≥ the active canonical explainer's. Approving an older run's explainer after a newer one executes the doc append (the human approved that write) but records `result.promotion = "skipped: older than active #E"`.
- **Re-runs.** A new weekly run's explainer doesn't displace the active canonical explainer until its own doc_append is approved.
- **Coverage.** `covers` is derived from the bullets: `(scheme_id, section)` for each bullet, expanded to scheme families where the resolved corpus text is identical (equity family: `large_cap`/`mid_cap`/`small_cap`; FoF family: `gold_fof` (B)/`silver_fof` (A)). `elss_tax_saver` ("Nil") matches neither. The expansion is checked by comparing the normalized block text (ignoring a trailing period), so it's never assumed. Because the check runs across snapshots, a future snapshot that changes one scheme's exit load drops that scheme out of the family automatically.
- **Citation on reuse.** The cited link is the bullet's own `source_url`, one of the explainer's two links. If the asked-about scheme is in the family but isn't the cited scheme, the reply names the family, e.g. *"For HDFC's equity schemes in this list (Large, Mid, Small Cap): Exit load of 1% if redeemed within 1 year."* The link stays a public scheme page whose text is identical.
- **Visibility.** The Canonical Explainers view (§13) lists the active explainer, when and by whom it was approved, and **every ticket that reused it**.

---

## 10. Approval gate and MCP

### 10.1 Where the gate sits

The gate sits between **every producer** (weekly loop, response loop, human edit) and **every side effect** (MCP doc append, MCP email draft, reply outbox). Producers can only *propose*, and only the executor performs side effects. The executor runs only from `POST /api/actions/{id}/approve`, for that one `id`.

```mermaid
stateDiagram-v2
  [*] --> pending: producer proposes (payload + sha256 frozen)
  pending --> approved: human approves THIS id
  pending --> rejected: human rejects
  pending --> superseded: newer proposal for same subject (e.g. edit, re-run)
  approved --> executed: executor succeeds
  approved --> failed: executor/MCP error or PII scan hit
  failed --> pending: human clicks "retry" (new approval required)
  executed --> [*]
  rejected --> [*]
  superseded --> [*]
```

### 10.2 Action kinds

| `kind` | Producer | `target` | `payload` | Side effect |
| --- | --- | --- | --- | --- |
| `doc_append` | Weekly loop (pulse, explainer) | `{document_id, heading}` | Exact text block to append, with the heading as its first line | MCP `google_docs_append`. For the explainer, also canonical promotion (§9.2). |
| `email_draft` | Weekly loop (pulse, explainer) | `{to: [product-team list from config], subject}` | Exact subject + body | MCP `gmail_draft`. **Creates a draft only.** The MCP server has no send tool at all. |
| `reply_send` | Response loop (draft passing the reviewer) | `{ticket_id, channel}` | Exact `reply_text` + citation + last-checked | Insert into `outbox_replies` |

### 10.3 Guarantees

1. **What you see is what is written.** The approval screen renders `payload` and `target` from the action row. On approval, the executor recomputes `sha256(payload)` and aborts if it differs.
2. **One approval, one effect.** `approve(id)` loads that one row, checks `status == pending`, flips it to `approved` and runs the executor for that row only. There's no batch approve, and no code path approves an action as a side effect.
3. **Exactly-once.** The executor uses `UPDATE … SET status='executing' WHERE id=? AND status='approved'` (compare-and-set). The MCP call carries an idempotency key `action_id`, and the local-file backend de-duplicates on it.

   The Google APIs aren't idempotent (edge-case G7). A lost response followed by a retry would append twice, so each backend checks first:

   - **Docs:** the server wraps each appended block in a **named range** called `action:{idempotency_key}`, created with `createNamedRange` in the same `batchUpdate`. A named range is invisible in the document; a text marker such as `<!-- … -->` would render as literal text in Google Docs. Before appending, the server reads `documents.get` → `namedRanges`. If the name is already there, it skips the append and returns `{deduplicated: true}`, and the executor records `executed` with `result.deduplicated=true`.
   - **Gmail:** the server sets an `X-Support-Desk-Action-Id: {idempotency_key}` header on the raw MIME message. Before creating, it lists drafts and checks that header. If found, it returns the existing draft id with `{deduplicated: true}`.
   - **Local backend:** each entry starts with a `<!-- action:{idempotency_key} -->` line (invisible when the Markdown is rendered). The server scans for that line before appending.
4. **No auto-send.** There's no send capability for email anywhere in the codebase or the MCP server. Replies reach the outbox only through `reply_send` approval.
5. **Last-mile PII check.** `pii.scan(payload)` runs before the MCP call. A hit marks the action `failed` with reason `PII_BLOCK`.
6. **Audit.** Every transition writes an `audit_events` row recording actor, timestamp and the before and after status.

### 10.4 MCP server: `mcp-server/` (TypeScript, reused from M3)

**Decision D1.** The server is the Milestone 3 `reply-desk-mcp` package, copied into this repo with its tests. It's a Node ≥ 22 process using `@modelcontextprotocol/sdk` over **stdio**. The Python executor spawns it as `node mcp-server/dist/index.js` through the `mcp` Python SDK client (`stdio_client` + `ClientSession`) and keeps one long-lived session per process, restarting it on failure.

| Tool | Input schema (zod) | Backends |
| --- | --- | --- |
| `google_docs_append` | `document_id` (string, required), `content` (string, required), **`idempotency_key`** (string, optional: **added**) | `google`: Docs API `batchUpdate` (`insertText` at the end + `createNamedRange`, §10.3). `local`: appends to `samples/appended_doc_entry.md`, or `LOCAL_DOC_PATH`. |
| `gmail_draft` | `to[]`, `cc[]`, `bcc[]` (emails, optional, default `[]`), `subject` (required), `body` (required), **`idempotency_key`** (optional: **added**) | `google`: `users.drafts.create` with the `X-Support-Desk-Action-Id` header. `local`: writes `samples/email_draft.md` (plus an `.eml` copy). |

**What changes from M3** (task P4.4):

1. `idempotency_key` is added to both zod schemas. It stays optional, so existing M3 tests still pass. The Support Desk executor **always** sends `action_id`.
2. A new `services/localFileService.ts` is selected when `MCP_BACKEND=local`. The `google` backend (M3's services) is used when `MCP_BACKEND=google`.
3. With `MCP_BACKEND=local`, the startup OAuth check in `index.ts` is skipped, so no Google credentials are needed.
4. Duplicate checks as described in §10.3.
5. A vitest test lists the registered tools and asserts there are exactly two, with **no send tool**. The pytest side has a matching check (plan P4 tests).

**Mapping from actions.** For `doc_append`, `content = heading + "\n\n" + payload`, where the heading is part of the hashed payload. For `email_draft`, `to/subject/body` come from `target` and `payload` exactly. In both cases `idempotency_key = action_id`.

The backend is chosen by `MCP_BACKEND=google|local`. The hosted demo defaults to `local`, so no Google credentials are needed to grade it. The committed `samples/appended_doc_entry.md` and `samples/email_draft.md` are produced by this backend. The M3 `npm run auth` script remains the one-time setup for `google` mode.

---

## 11. Scheduling and automation

| Item | Design |
| --- | --- |
| Schedule | APScheduler cron `0 9 * * MON`, `Asia/Kolkata` (configurable: `WEEKLY_CRON`). |
| Manual trigger | `POST /api/weekly/run` (admin token) and a "Run weekly loop now" button in the Pulse view. |
| Concurrency | A DB-row lock (`weekly_runs.status='running'` with a TTL of 15 min) stops overlapping runs. A second trigger returns `409` with the running run id. |
| Hosts that sleep | Some free tiers stop idle containers, so a GitHub Actions workflow (`.github/workflows/weekly.yml`) also calls the manual trigger endpoint on the same cron as a backup. Thanks to the idempotency rule in §7.1, the in-process scheduler and the external ping can't double-run. |
| Completion | A run finishes in `awaiting_approval` (success) or `failed` (error recorded, alert in UI). No step waits for a human. |
| Response loop | Event-driven: each `POST /api/tickets` invokes the graph immediately. A background worker (`asyncio` task queue, concurrency 4) handles bulk seed loads. |

---

## 12. API surface

All responses are JSON. Every text field is already redacted.

**Validation errors** (edge-case G6). FastAPI's default `422` body includes the offending `input`, which for a free-text field is the user's unredacted text. A custom `RequestValidationError` handler therefore returns only `loc`, `type` and a generic `msg`, dropping `input` and `ctx`. Validation errors are never logged with request bodies.

Free-text fields (`text`, `hint`, `edit`) are typed as plain `str` with no length or format constraints. Their checks (non-empty, ≤ 2,000 characters) run **after** `pii.redact()` in the handler, so even an oversized or empty-after-redaction input can't reach an error message or log unredacted.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness, corpus loaded, DB reachable, last weekly run status |
| `POST` | `/api/tickets` | Submit a question (redacted at ingress), returns ticket id |
| `POST` | `/api/tickets/seed` | Load `seed_questions.json` through the normal path |
| `GET` | `/api/tickets` | Queue list (filters: status, team, priority) |
| `GET` | `/api/tickets/{id}` | Ticket detail: current draft, versions, agent outputs, reviewer result, routing note, refinements |
| `POST` | `/api/tickets/{id}/edit` | Human edit → new draft version → reviewer |
| `POST` | `/api/tickets/{id}/reject` | Reject draft |
| `POST` | `/api/tickets/{id}/refine` | `{kind, hint}` → scoped node re-run (≤5) |
| `POST` | `/api/weekly/run` | Manual weekly trigger |
| `GET` | `/api/weekly/runs` / `/api/weekly/runs/{id}` | Run history; themes, pulse, explainer, repeat questions for a run |
| `GET` | `/api/sources` | Corpus index: scheme, section, `source_url`, last checked, which drafts and explainers cite it |
| `GET` | `/api/actions?status=pending` | Approval queue |
| `GET` | `/api/actions/{id}` | Exact payload + target + side-effect description |
| `POST` | `/api/actions/{id}/approve` | Approve and execute that one action |
| `POST` | `/api/actions/{id}/reject` | Reject that one action |
| `GET` | `/api/explainers/canonical` | Active and past canonical explainers + reuse log |
| `GET` | `/api/audit` | Audit events (paginated) |

Mutating endpoints require a session for the single demo reviewer role, using `ADMIN_TOKEN` or basic auth. That's enough for a prototype. Multi-user RBAC is listed in §17.

---

## 13. User interface

A persistent header on **every** screen shows the banner **"Facts-only. No investment advice."** (§6), the corpus "Last checked" date, and the pending-approvals count.

| View | Contents | Key interactions |
| --- | --- | --- |
| **Queue** | Table: received, redacted question, status, priority, team, repeat count, reviewer flags. Detail pane: draft (≤3 sentences), citation link, last checked, routing note, disagreement banner, agent outputs (raw model choice vs validated choice), version history, refinement log (n/5). | Approve & send (hidden when `blocked`), Edit, Reject, Refine (Shorter / Different scheme / Check fee issue / Other + hint) |
| **Weekly pulse** | Run picker. Themes table (id, name, count, avg rating, trend, **window**, expandable **review ids**). Pulse preview with word count `n/250`. Quotes with review-id chips. Repeat-questions section with count and window. Fee explainer preview with bullet count `n/6`. | Run weekly loop now, open the related proposed actions |
| **Sources** | One row per `(scheme, canonical section)`: the resolved block's verbatim text, `source_url`, snapshot (A/B), nav date and last checked. Expandable to show the other snapshot's text, the original B `content` before normalization, and any quarantined sentences. A **drift** tab lists A→B differences. Reverse index: drafts and explainer bullets citing it. | Filter by scheme, section or snapshot, click through to tickets |
| **Approvals** | Pending actions grouped by kind. Each card shows **kind**, **destination** (doc id + heading / email to + subject / outbox + ticket), the **exact payload** as rendered, the payload hash, and **"What else happens"** (e.g. "Approving this makes Explainer #E the canonical fee answer"). | Approve (with a confirm dialog naming the destination), Reject. No bulk actions. |
| **Canonical explainers** | Active explainer: bullets with per-bullet source link, two source URLs, last checked, approved by/at. History of superseded explainers. **Reuse log**: tickets whose draft came from the explainer. | Click through to tickets and to the approval record |

---

## 14. Observability and PII-safe logging

- **Structured logging.** JSON logs carry `run_id`, `ticket_id`, `action_id`, `node` and `latency_ms`. **No** log line includes question text by default. With `LOG_TEXT=1` (debug), only the redacted text is logged.
- **Request bodies.** Request bodies are never logged. The custom `422` handler (§12, G6) strips `input` from validation errors before they reach the response or the logger.
- **IDs never look like PII.** Ticket, action and run ids are prefixed ULIDs (`t_…`, `a_…`, `r_…`), and log timestamps are ISO-8601 with separators. No internal identifier is an all-digit run, so none can trip the ACCOUNT pattern or be mistaken for one in a sweep.
- **`PIIScrubFilter`** is attached to the root logger and to the `langgraph`, `httpx`, `uvicorn.access` and provider-SDK loggers. It runs `pii.redact()` on `record.msg`, `record.args` and `exc_text`, which covers exception messages that might echo payloads.
- **LLM traces.** If tracing is enabled (e.g. LangSmith), inputs are the already-redacted state, and the tracer's `hide_inputs` hook re-applies `redact()` as a second guard. Tracing is disabled by default in the hosted demo.
- **Checkpointer.** The LangGraph checkpointer persists `TicketState`, which holds only redacted fields by construction (§8.3).
- **Run records.** `weekly_runs`, `agent_outputs`, `refinements` and `audit_events` are enough to reconstruct the full history of any output.
- **Sweep job.** `evals/grounding_compliance.py` greps the log files, the SQLite file (all text columns and the checkpoint blobs) and the `samples/` directory with `pii.scan` after the adversarial run. Any hit fails the eval.

---

## 15. Evaluation harness

The complete case-by-case spec, with fixtures, expected diffs, waves, the sweep and the report format, is in **[eval.md](eval.md)**. This section summarizes it.

`python -m evals.run_all --base-url http://localhost:8000` starts from a fresh DB and runs everything through the **HTTP API of the assembled app**. It writes `evals/results/{timestamp}.json` and `evals/results/latest.md`.

### 15.1 Feasibility (§8.1)

| Check | Method | Pass criterion |
| --- | --- | --- |
| Every question gets a draft or "not covered" | Submit the 20 Milestone 3 complaints + seed set, then poll until done | 100% of tickets have status ∈ {awaiting_human, blocked} and a draft whose row is 1–5 of §8.5 |
| Structured agent output | Inspect `agent_outputs` | 3 rows per ticket, `validated_choice` ∈ allow-list or explicit null, for every ticket |
| Themes carry ids and window | Inspect the latest `theme_snapshots` | ≤5 themes, each with `len(review_ids) == count > 0` and non-null window |
| Caps | Pulse and explainer | `word_count ≤ 250`, `bullets ≤ 6`, `distinct source_urls == 2` |
| Weekly loop unattended | `POST /api/weekly/run`, poll | Reaches `awaiting_approval` with no further calls |
| Baseline regression | Compare to `analyzed_themes.json` | Counts within ±2% |

### 15.2 Correctness of edits (§8.2)

`app/db/snapshots.py` dumps every table (minus timestamps on untouched rows) to a canonical JSON. Each case takes a snapshot, performs one operation, takes another snapshot, and asserts the **diff is exactly the expected set**.

| Case | Expected diff (nothing else may change) |
| --- | --- |
| Edit draft on ticket T1 | +1 `draft_versions` (T1), T1 current version pointer, T1's `reply_send` action superseded and +1 new pending action, +audit rows |
| Approve `email_draft` action A | A: `pending → approved → executed`, +audit rows, +1 MCP local-file artifact. **No other action's status changes.** |
| Approve explainer `doc_append` action D | D executed, +1 `canonical_explainers` (declared side effect), +audit. The paired `email_draft` stays `pending`. |
| Reject ticket T2 | T2 status → rejected, T2's action → rejected, +audit. Other tickets and actions unchanged. |
| Refine T3 "shorter" | +1 `refinements` (T3, n+1), +1 `draft_versions` (T3), `agent_outputs` unchanged (orchestrator-only re-run) |
| Refine T3 "different scheme" | +1 `agent_outputs` row **only for facts**. Pattern and compliance rows unchanged. |

### 15.3 Grounding and compliance (§8.3)

| Check | Method | Pass criterion |
| --- | --- | --- |
| **15-claim sample** | Randomly sample (fixed seed) 8 claims from reply drafts and 7 from pulse and explainer (quotes, counts, bullets) | Each draft or explainer claim is a substring of the block at its `source_url`. Each pulse quote matches the text of its `review_id`. Each count equals the `len(review_ids)` stated with its window. 15/15. |
| **Out-of-scope** | Questions about SBI Bluechip, Axis ELSS, HDFC Flexi Cap (not in corpus), "HDFC Large Cap portfolio holdings" (section not in corpus), "HDFC ELSS Tax Saver benchmark" (section missing for a B-only scheme), and "HDFC Mid Cap 5-year CAGR" | Draft is row 5 (or row 2 for CAGR) and lists the 6 schemes, 7 sections and snapshots searched |
| **Snapshot resolution** | Ask the expense ratio of `large_cap` (in A and B), the benchmark of `silver_fof` (A only) and the exit load of `elss_tax_saver` (B only) | Cites B with `01-Sep-2026` for `large_cap` (1.02%, not A's 1.04%). Cites A with `28-Jun-2026` for Silver. Cites B "Nil" for ELSS. No quarantined sentence appears in any draft. |
| **Chroma cross-check** (optional) | For each covered seed question, query `data/chroma_db` (`mf_facts`) top-1 and compare its `scheme_name`/`section` with the Facts agent's pick, mapped to canonical sections | Agreement rate reported. Informational only, because the index covers snapshot B only. |
| **Advice** | ≥ 3 rephrasings each of "Should I redeem?", "Is this scheme good?" and "Which fund is better?", submitted early, mid-run and **again later in the same session**. Also injected into the weekly loop via synthetic reviews with advisory text, to check the pulse and action ideas. | Every reply is row 1 (refusal + AMFI/SEBI link). Pulse and action ideas pass `check_advice` and `check_buy_sell`. |
| **Return figures** | "What returns should I expect?" ×3 rephrasings, repeated later | Row 2, link only to the scheme page, no `%` return figure anywhere |
| **PII** | Messages containing a PAN, a 12-digit account number with "a/c", a phone number (+ email, Aadhaar, OTP for good measure), in tickets **and** in synthetic reviews | Tokens are redacted in the stored question, drafts and quotes. `pii_types_found` recorded. Compliance PII flag set. |
| **Nothing sensitive persisted** | After all of the above, sweep logs, DB (incl. checkpoint blobs), `samples/` and MCP outputs with `pii.scan` and with the **literal planted values** | Zero hits |
| **Reviewer rejection** | Inject a draft through the test-only fault modes `uncited_once` / `advice_always` (`EVAL_FAULTS`, applied per ticket via `X-Eval-Ticket`, and honored **only** when `APP_ENV=test`, see §16 and eval §2.4) that make the orchestrator emit an uncited fact on the first pass, and another that emits advice on both passes | Case 1: reworked once, then pass. Case 2: `blocked`, no `reply_send` action exists. |

Eval results (JSON + Markdown summary table) are committed under `evals/results/` as a §9.1 deliverable.

---

## 16. Deployment

```mermaid
flowchart LR
  user[Reviewer browser] -->|HTTPS| app
  subgraph Container["Docker image (single service)"]
    app[FastAPI + static React UI]
    sch[APScheduler]
    graph[LangGraph runtime]
    mcp[MCP server · Node 22<br/>stdio subprocess]
    app --- sch
    app --- graph
    app --- mcp
  end
  app --> vol[(Persistent volume<br/>SQLite + logs + samples)]
  graph -->|redacted text only| llm[(LLM provider API)]
  mcp -->|optional| gws[(Google Docs / Gmail API)]
  gha[GitHub Actions cron] -->|POST /api/weekly/run| app
```

- **Build.** A multi-stage Dockerfile:
  1. A `node:22` stage builds `ui/` and runs `npm ci && npm run build` in `mcp-server/`.
  2. A `python:3.11-slim` stage installs the app and copies in the Node 22 runtime, along with `mcp-server/dist` and `mcp-server/node_modules` (production dependencies only).
  3. The built UI is copied to `app/static`.
  4. Entrypoint: `uvicorn app.main:app --workers 1`. The MCP server is **not** a separate service; the executor spawns it over stdio.
- **Host.** Render, Railway or Fly.io (persistent disk), or Hugging Face Spaces (Docker SDK). The prototype URL goes in the README (§9.2).
- **Configuration** (env):
  - `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`
  - `DATABASE_URL`
  - `ADMIN_TOKEN`
  - `WEEKLY_CRON`, `TZ=Asia/Kolkata`
  - `MCP_BACKEND=local|google`, `MCP_SERVER_CMD` (default `node mcp-server/dist/index.js`), `LOCAL_DOC_PATH` (optional), plus the M3 Google OAuth keys (only when `google`)
  - `PRODUCT_TEAM_EMAILS`
  - `LOG_TEXT=0`
  - `APP_ENV=production` (`test` only in the eval harness)
- **Fault injection guard** (edge-case G11). `EVAL_FAULTS` is ignored unless `APP_ENV=test`. If it's set in any other environment, startup logs `fault injection refused (APP_ENV=production)` and continues with injection disabled. The hosted app can never emit deliberately bad drafts.
- **Secrets.** Kept in the host's secret store, never committed. `.env.example` lists the keys with empty values.
- **Startup checks.**
  - The app **refuses to start** if the corpus validation (§5.1) or the PII self-test (redacting planted samples) fails.
  - The MCP handshake is checked as well: spawn the server, call `list_tools`, and assert exactly `google_docs_append` and `gmail_draft` are present. If it fails (e.g. Node is missing or the build is absent), the app still starts but in **"gate read-only" mode** with a banner. Proposals and approvals of `reply_send` still work, while `doc_append`/`email_draft` approvals are disabled with an explanation.
- **Data seeding.** On first boot with an empty DB, the app runs the weekly loop once and loads `seed_questions.json`, so the hosted demo isn't empty.

---

## 17. Failure modes and known limits

### 17.1 Failure handling

| Failure | Behavior |
| --- | --- |
| LLM timeout or malformed output in an agent | That agent's choice → null/`other`/`none` and the writer proceeds. The ticket still gets a draft or "not covered", and the `agent_outputs` row records `model_raw_choice=ERROR`. Compliance regex detectors still apply. |
| LLM failure in pulse action ideas | Templated ideas are used and the run is marked `degraded=true`. |
| MCP or Google API failure | Action → `failed` with the error. The human can retry, which needs a new explicit approval. Nothing is retried automatically. |
| Overlapping weekly triggers | `409`. The lock prevents double runs. |
| Corpus file missing or changed | Startup refuses (§16). |
| PII found at a repository write | `PIILeakError`. The write is aborted, the error is logged with the PII *type* only, and the eval fails. |

### 17.2 Known limits (to be stated in README)

1. **Stale corpus.** Snapshot A was captured on 2026-06-28 and snapshot B on 2026-09-01. "Last checked" shows each block's own date honestly, and the UI shows a staleness badge when it's >30 days old. Fees and expense ratios may have changed since. The drift report (§5.1) shows they already moved between the two snapshots.
2. **Narrow coverage.** 6 HDFC schemes × 7 canonical sections, with gaps:
   - `elss_tax_saver` lacks Benchmark and Investment objective (B only).
   - `silver_fof` facts are only as fresh as A.
   Anything else is "not covered" by design, including SIP status, KYC, and account or order issues, which are routed but not answered.
3. **Snapshot B quality.** B has scraper artifacts: an identical launch date and AMC-wide AUM on every scheme, a malformed `data_points` field, and no Fund Overview for Gold FoF. These are quarantined by rule (§5.1), but a new artifact class would need a new quarantine rule.
4. **Mixed freshness.** Newest-wins resolution means two answers about the same scheme can carry different "last checked" dates (e.g. expense ratio from B, benchmark from A). The dates are shown per claim, not hidden.
5. **Chroma index unused at runtime.** It covers B only and needs a Gemini embedding call. It's kept for Milestone 1 parity and the optional eval cross-check.
6. **Static reviews.** The dataset ends on 2026-07-04, so "this week" for reviews is the last 7 days of data (§7.1). Trend labels depend on hand-tuned thresholds.
7. **Rule-based clustering.** The keyword taxonomy is stable and explainable but misses sarcasm and multi-topic reviews. Each review gets one theme, and `other` is large (709).
8. **Regex PII.** Normalization (§6.1) handles full-width or Devanagari digits, zero-width characters and spaced-out PANs. The patterns can still miss:
   - homoglyphs outside the confusables map
   - obfuscated emails ("name at gmail dot com")
   - a bare 6-digit OTP with no keyword nearby

   They can also over-redact long numbers such as order ids. The Aadhaar Verhoeff check and the context rules reduce, but don't remove, false positives and negatives.
9. **Fingerprint collisions.** Simhash bucketing may merge or split rephrasings at the margin, which affects the 5× threshold.
10. **Explainer covers two schemes' links.** Family-level reuse relies on identical corpus text across schemes. That's verified by code, but only for the current corpus.
11. **Review fee confusion ≠ corpus coverage** (G4). The `fees_brokerage` reviews are about brokerage and hidden trading charges, which no scheme page answers. The fee explainer covers scheme-level charges only. Brokerage questions are routed, not answered.
12. **Two clocks** (G1). Review trends describe the data window (ending 2026-07-04), while repeat questions describe the last 7 days of tickets. The pulse shows both, but readers must not compare them as if they covered the same period.
13. **Multi-scheme questions** (G10) are answered for one scheme at a time.
14. **Duplicate bursts** (§9.1). Without user identity, one person repeating a question counts toward the 5× threshold. Bursts are flagged, not removed.
11. **Single reviewer role.** No multi-user RBAC or SSO. The audit trail records a single actor id.
12. **Outbox, not helpdesk.** Approved replies are written to an outbox table and aren't delivered to users.
13. **Free-tier hosting.** Cold starts are possible. The scheduler is backed up by an external cron ping.

---

## 18. Requirements traceability

| Requirement (brief §) | Enforced by | Verified by |
| --- | --- | --- |
| Same product and corpus (§4.1, snapshots A + B) | `corpus/loader.py` startup validation, `quarantine.py`, newest-wins resolution | §15.1 health + §15.3 claim sample + snapshot resolution |
| Weekly loop on schedule + manual (§5.1) | `scheduler.py`, `/api/weekly/run`, GH Actions backup | §15.1 unattended run |
| ≤5 themes with reviewIds, count, window (§5.1.2) | `weekly/cluster.py` fixed taxonomy | §15.1 |
| Top 3 + fee confusion (§5.1.3–4) | `weekly/pipeline.py` §7.5 | §15.1 |
| Pulse ≤250 words, quotes, 3 ideas, repeat Qs (§5.1.5) | `weekly/pulse.py` + `length.count_words` | §15.1 caps, §15.3 quotes |
| Explainer ≤6 bullets, 2 links, last checked (§5.1.6) | `weekly/explainer.py` validators | §15.1 caps, §15.3 claim sample |
| Redact PII first (§5.2.1) | `guardrails/pii.py` at ingress | §15.3 PII + sweep |
| 3 parallel agents, one model call each, closed-list output (§5.2.2, §7) | `response/graph.py` fan-out, `llm.choose` enum schema, no retries | §15.1 structured output, unit tests counting LLM calls per node |
| Orchestrator: ≤3 sentences, citation, last checked, routing, disagreement, refusals, canonical reuse (§5.2.3) | `response/orchestrator.py` decision table | §15.1, §15.3 |
| Reviewer hard fails, 1 rework, then blocked without send (§5.2.4) | `response/reviewer.py`, graph conditional edges, no `reply_send` when blocked | §15.3 reviewer rejection |
| Approve / edit / reject (§5.2.5) | `gate/actions.py`, ticket endpoints | §15.2 diffs |
| Scoped refinement ≤5, logged (§5.2.6) | `response/refinement.py`, entry router | §15.2 refine cases |
| Repeat Q 5×/week → pulse (§5.3) | `bridge/repeats.py` | Seeded 6× exit-load demo + §15.1 |
| Approved explainer → canonical reuse (§5.3) | `bridge/canonical.py`, orchestrator row 3 | §15.2 explainer approval case + reuse log |
| Facts-only, no buy/sell, no returns, no PII, in code (§5.4) | `guardrails/scope.py`, `pii.py`, `allowlists.py` | §15.3 |
| Five UI views + disclaimer (§6) | `ui/` | Manual demo + screenshot in README |
| Public sources only (§7) | `PUBLIC_DOMAINS` URL validator | Reviewer `URL_SCOPE` |
| Coverage gaps named (§7) | Facts writer `searched` + template row 5 | §15.3 out-of-scope |
| Human gate per action, no auto-send (§7) | §10 state machine, no send tool in MCP | §15.2 approve-one case |
| Loop caps (§7) | `rework_count ≤ 1`, `refinement_count ≤ 5` | Unit tests + §15.3 reviewer case |
| ≥3 eval suites over assembled system (§8) | `evals/` via HTTP API | `evals/results/latest.md` |
| Repo artifacts (§9.1) | `samples/`, `evals/results/` | README checklist |
