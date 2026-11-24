# Deploying The Support Desk (P11)

> **Facts-only. No investment advice.**

One Docker image runs everything: FastAPI, the built React UI, the weekly scheduler, the LangGraph runtime and the MCP server (spawned over stdio). Architecture: [§16](architecture.md#16-deployment). Plan: [P11](implementation-plan.md).

## What's in the repo

| File | Purpose |
| --- | --- |
| `Dockerfile` | Stage 1 (`node:22`) builds `ui/` and `mcp-server/`. Stage 2 (`python:3.11-slim`) runs the app with the Node 22 runtime. `uvicorn --workers 1`. |
| `docker/entrypoint.sh` | Creates `/data/state/samples`, gives the disk to the `app` user, then drops root. |
| `render.yaml` | Render blueprint: one Docker web service, a 1 GB disk at `/data/state`, health check `/api/health`, env vars. |
| `scripts/smoke.py` | Smoke test (standard library only): health, seeding, UI, one weekly run, one ticket, one approval. |
| `.github/workflows/ci.yml` → `docker` job | Builds the image, runs it, smoke-tests it, restarts it and checks the state survived. |
| `.github/workflows/weekly.yml` | Backup weekly trigger for hosts that sleep. Sends `{"trigger": "cron"}`, so it can't double-run with the in-app scheduler. |

## 1. Create the service on Render

1. Render dashboard → **New → Blueprint** → connect `ganeshkondaveeti/support-desk` → it reads `render.yaml`.
2. The blueprint asks for the three `sync: false` values. Set them in **Environment**:

   | Key | Value |
   | --- | --- |
   | `GROQ_API_KEY` | Your Groq key. Without it the app falls back to the rules-only backend (still safe, lower quality). |
   | `ADMIN_TOKEN` | A long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Reviewers enter it in the UI when they first approve, edit or run something. **Required:** in production, mutating routes return 401 without it. |
   | `PRODUCT_TEAM_EMAILS` | Optional, comma-separated recipients for the email drafts. |

3. Deploy. The first boot runs the weekly loop once and loads `data/seed_questions.json`, so the demo isn't empty. That takes about a minute; `/api/health` shows `"seeded": true` when it's done.

**Plan and disk.** A persistent disk needs a paid instance (`starter` in `render.yaml`). On a free instance there's no disk: remove the `disk:` block, and every restart or spin-down starts from an empty database that first-boot seeding refills. Approvals and canonical explainers are then lost on restart. Say so in the README's known limits if you go that way.

**Google Docs / Gmail.** `MCP_BACKEND=local` writes approved docs and email drafts to `/data/state/samples/`. To write to Google instead, set `MCP_BACKEND=google` plus the `GOOGLE_*` values from M3's `npm run auth` (`GOOGLE_TOKEN_JSON` holds the token, since the container has no browser).

## 2. Check the startup checks

In the service's **Logs** after a deploy:

- `startup pii self-test passed samples=6`. If redaction is broken, the app refuses to start instead.
- `startup mcp handshake ok tools=['gmail_draft', 'google_docs_append']`. If the MCP server can't start, the app still runs, `/api/health` shows `"gate_mode": "read_only"`, the UI shows a banner, and doc/email approvals return 409 while reply approvals keep working.
- `corpus loaded schemes=6 blocks=40`. A corpus that fails validation stops the app.

## 3. Smoke test the hosted URL

```bash
ADMIN_TOKEN=<the token> python scripts/smoke.py https://<your-service>.onrender.com
```

All six checks should print `PASS`. The script writes one weekly run, one ticket and one outbox reply, so run it against the demo instance only.

## 4. Point the backup cron at it

The in-app scheduler runs every Monday 09:00 IST. Hosts that sleep can miss it, so `weekly.yml` also calls the API at the same time. Give it the URL and token as repository secrets:

```bash
gh secret set SUPPORT_DESK_URL --repo ganeshkondaveeti/support-desk --body "https://<your-service>.onrender.com"
gh secret set ADMIN_TOKEN --repo ganeshkondaveeti/support-desk   # paste the token when prompted
```

Then run it once by hand: **Actions → Weekly loop backup → Run workflow**. A `200` with `"noop": true` means the scheduler already ran this week's input; `409` means a run was in progress. Both are fine.

## 5. Exit criteria (CP4)

- The hosted URL loads, `/api/health` shows `seeded: true`, and the five views work.
- A manual weekly run succeeds (step 3 does one).
- The scheduled run fires at the next Monday 09:00 IST, or the backup cron does. Check **Weekly pulse → run picker** for a run with trigger `cron`.
