# Evaluation results — 2026-11-25 12:06 IST
Mode: graded · Backend: groq / openai/gpt-oss-120b · Seed 42 · git cc66044,91480fe
Facts-only. No investment advice.

**Notes**

- Interim report: edit and feasibility only. The graded run is split one suite per day to fit Groq's free tier; grounding and quality follow, and this report is then replaced by the four-suite merge.
- FEAS-13 in the feasibility run (20261126-084841) ran before the llm_down fix (ed67cb3) and passed without checking degraded; re-checked alone in graded mode after the fix, it passed with degraded=true.

| Suite | Cases | Passed | Status |
|---|---:|---:|---|
| 1. Feasibility | 13 | 13 | ✅ |
| 2. Correctness of edits | 18 | 18 | ✅ |
| 3. Grounding & compliance | — | — | ℹ️ |
| 4. Agent quality | — | — | ℹ️ |

Headline numbers: PII sweep: 0 hits in 12 locations

## Failures
(none)

## Per-case detail
### edit
- EDIT-01: pass (diff_ok=True)
- EDIT-02: pass (diff_ok=True)
- EDIT-03: pass (diff_ok=True)
- EDIT-04: pass (diff_ok=True)
- EDIT-05: pass (diff_ok=True)
- EDIT-06: pass (diff_ok=True, canonical_row_3=True)
- EDIT-07: pass (diff_ok=True)
- EDIT-08: pass (diff_ok=True)
- EDIT-09: pass (diff_ok=True)
- EDIT-10: pass (diff_ok=True)
- EDIT-11: pass (diff_ok=True)
- EDIT-12: pass (diff_ok=True)
- EDIT-13: pass (diff_ok=True)
- EDIT-14: pass (diff_ok=True)
- EDIT-15: pass (diff_ok=True)
- EDIT-16: pass (diff_ok=True)
- EDIT-17: pass (diff_ok=True)
- EDIT-18: pass (diff_ok=True)

### feasibility
- FEAS-01: pass (tickets=93, ok=93)
- FEAS-02: pass (tickets=93)
- FEAS-03: pass (errors=3, total=279, rate=0.0108)
- FEAS-04: pass (themes=5, reviews=1650)
- FEAS-05: pass (drifts=[], used_baseline_fallback=True)
- FEAS-06: pass (checked=12, max_words=244)
- FEAS-07: pass (bullets=6, urls=2)
- FEAS-08: pass (run_id=wr_3a1-c7b-0d9-e25-422-ea3-ed6-aa9-646-705-60, pending_weekly=4)
- FEAS-09: pass (concurrent_409=5, cron_noop=True, manual_run=wr_e7b-bfd-b5a-f4b-4e8-f9a-767-b62-e66-35f-2b, superseded=4)
- FEAS-10: pass (exit_load_count=8, clusters=1)
- FEAS-11: pass (run_id=wr_217-e99-3e9-fb3-4bb-096-2ec-3ae-1cd-1e6-b8, rows=[4, 4, 3], elss_row=4)
- FEAS-12: pass (tickets=93)
- FEAS-13: pass (degraded=False, tickets=5, run_id=wr_3d2-8f1-92a-f11-481-e9e-2de-347-b00-75e-18)
