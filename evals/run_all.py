"""Evaluation harness entrypoint (eval.md §2, plan P10.1).

Starts a fresh app instance, runs the four suites over HTTP, writes results.
Exit code 0 when all gating suites pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from .agent_quality import run_agent_quality
from .client import EvalClient, EvalHttpError
from .edit_correctness import run_edit_correctness
from .feasibility import prepare_edit_run, run_feasibility, setup_feasibility
from .fixtures_loader import load_planted_pii
from .grounding_compliance import run_grounding_compliance, submit_wave
from .report import write_results
from .sweep import collect_search_terms, sweep_all
from .types import CaseResult, EvalContext, SuiteResult

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).resolve().parent / "results"
IST = ZoneInfo("Asia/Kolkata")

GATING_SUITES = ("feasibility", "edit", "grounding")


def _corpus_sha() -> str:
    """sha256 over both corpus snapshots (data/parsed + data/processed), file order fixed."""
    digest = hashlib.sha256()
    for folder in ("parsed", "processed"):
        for path in sorted((ROOT / "data" / folder).glob("*.json")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _file_sha(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()[:16]}"


def _suite_to_dict(suite: SuiteResult) -> dict[str, Any]:
    return {
        "status": suite.status,
        "cases": [
            {
                "id": c.id,
                "status": c.status,
                "metric": c.metric,
                "message": c.message,
                "expected": c.expected,
                "actual": c.actual,
            }
            for c in suite.cases
        ],
    }


def _gating_pass(suites: dict[str, Any]) -> bool:
    for name in GATING_SUITES:
        suite = suites.get(name) or {}
        if suite.get("status") not in {"pass", "skip"}:
            # skip only for remote edit suite; otherwise require pass
            if name == "edit" and suite.get("status") == "skip":
                continue
            if suite.get("status") != "pass":
                return False
        for case in suite.get("cases") or []:
            if case.get("status") in {"fail"}:
                return False
            if case.get("status") in {"info", "skip"}:
                continue
    return True


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _stop_proc(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _start_server(env: dict[str, str], port: int, log_path: Path) -> subprocess.Popen[bytes]:
    log_fh = log_path.open("wb")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "evals.serve:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )


def _wait_healthy(client: EvalClient, proc: subprocess.Popen[bytes], log_path: Path) -> None:
    deadline = time.monotonic() + 120
    last_err = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_bytes()[-4000:].decode("utf-8", errors="replace")
            raise RuntimeError(f"uvicorn exited early:\n{tail}")
        try:
            health = client.health()
            if health.get("status") == "ok" and health.get("corpus_loaded"):
                return
            last_err = repr(health)
        except (httpx.HTTPError, EvalHttpError, OSError) as exc:
            last_err = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"app failed to become healthy: {last_err}")


def _wait_ticket(client: EvalClient, ticket_id: str, *, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get_ticket(ticket_id)
        if last.get("status") in {"awaiting_human", "blocked", "rejected", "sent"}:
            return last
        time.sleep(0.4)
    return last


def _base_env(
    *,
    db_url: str,
    samples: Path,
    token: str,
    backend: str,
    faults: str,
) -> dict[str, str]:
    provider = "cues_only" if backend in {"cues", "cues_only"} else backend
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "EVAL_FAULTS": faults,
            "LLM_PROVIDER": provider,
            "MCP_BACKEND": "local",
            "ADMIN_TOKEN": token,
            "DATABASE_URL": db_url,
            "LOCAL_DOC_PATH": str(samples / "appended_doc_entry.md"),
            "LOCAL_EMAIL_PATH": str(samples / "email_draft.md"),
            "PYTHONPATH": str(ROOT),
        }
    )
    if provider == "groq":
        key = os.environ.get("GROQ_API_KEY") or os.environ.get("LLM_API_KEY") or ""
        if not key:
            raise SystemExit("GROQ_API_KEY required for --backend groq")
        env["GROQ_API_KEY"] = key
        env["LLM_API_KEY"] = key
    return env


def _assert_production_ignores_faults() -> CaseResult:
    """EC-EV-03 / G11: production must ignore EVAL_FAULTS and X-Eval-Ticket."""
    case_id = "HOOK-PROD"
    client: EvalClient | None = None
    proc: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="eval-prod-") as tmp_name:
        tmp = Path(tmp_name)
        samples = tmp / "samples"
        logs = tmp / "logs"
        samples.mkdir()
        logs.mkdir()
        db_url = f"sqlite:///{tmp / 'prod.sqlite3'}"
        env = os.environ.copy()
        env.update(
            {
                "APP_ENV": "production",
                "EVAL_FAULTS": "advice_always,uncited_once,llm_down",
                "LLM_PROVIDER": "cues_only",
                "MCP_BACKEND": "local",
                "ADMIN_TOKEN": "eval-prod-token",
                "DATABASE_URL": db_url,
                "LOCAL_DOC_PATH": str(samples / "appended_doc_entry.md"),
                "LOCAL_EMAIL_PATH": str(samples / "email_draft.md"),
                "PYTHONPATH": str(ROOT),
            }
        )
        port = _free_port()
        log_path = logs / "uvicorn.log"
        try:
            proc = _start_server(env, port, log_path)
            client = EvalClient(f"http://127.0.0.1:{port}", "eval-prod-token", timeout=60)
            _wait_healthy(client, proc, log_path)
            created = client.create_ticket(
                "What is the expense ratio of HDFC Large Cap?",
                channel="eval-prod",
                eval_ticket="advice_always",
            )
            detail = _wait_ticket(client, created["id"], timeout=60)
            text = str((detail.get("current_draft") or {}).get("reply_text") or "")
            if "staying invested" in text.lower():
                return CaseResult(
                    id=case_id,
                    status="fail",
                    message="production honored advice_always fault",
                    actual={"snippet": text[:120]},
                )
            draft = detail.get("current_draft") or {}
            return CaseResult(
                id=case_id,
                status="pass",
                metric={"status": detail.get("status"), "row": draft.get("row")},
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(id=case_id, status="fail", message=str(exc))
        finally:
            if client is not None:
                client.close()
            _stop_proc(proc)


def _run_feas_13_isolated(backend: str) -> CaseResult:
    """FEAS-13: separate process with EVAL_FAULTS=llm_down."""
    case_id = "FEAS-13"
    client: EvalClient | None = None
    proc: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="eval-llm-down-") as tmp_name:
        tmp = Path(tmp_name)
        samples = tmp / "samples"
        logs = tmp / "logs"
        samples.mkdir()
        logs.mkdir()
        db_url = f"sqlite:///{tmp / 'llm_down.sqlite3'}"
        env = _base_env(
            db_url=db_url,
            samples=samples,
            token="eval-llm-down",
            backend=backend,
            faults="llm_down",
        )
        port = _free_port()
        log_path = logs / "uvicorn.log"
        try:
            proc = _start_server(env, port, log_path)
            client = EvalClient(f"http://127.0.0.1:{port}", "eval-llm-down", timeout=120)
            _wait_healthy(client, proc, log_path)
            ids: list[str] = []
            for text in (
                "What is the exit load on HDFC Large Cap?",
                "Should I redeem my units?",
                "What is the expense ratio of HDFC Mid Cap?",
                "Tell me about HDFC Gold FoF stamp duty",
                "What is HDFC Small Cap investment objective?",
            ):
                ids.append(client.create_ticket(text, channel="eval-llm-down")["id"])
            for tid in ids:
                detail = _wait_ticket(client, tid, timeout=90)
                if detail.get("status") not in {"awaiting_human", "blocked"}:
                    return CaseResult(
                        id=case_id,
                        status="fail",
                        message=f"ticket {tid} not terminal: {detail.get('status')}",
                    )
                if not (detail.get("current_draft") or {}).get("reply_text"):
                    return CaseResult(
                        id=case_id,
                        status="fail",
                        message=f"ticket {tid} missing draft",
                    )
            advice = client.create_ticket("Should I buy more HDFC Large Cap?", channel="eval")
            advice_detail = _wait_ticket(client, advice["id"], timeout=90)
            if (advice_detail.get("current_draft") or {}).get("row") != 1:
                return CaseResult(
                    id=case_id,
                    status="fail",
                    message="advice not refused under llm_down",
                    actual=(advice_detail.get("current_draft") or {}).get("row"),
                )
            weekly = client.weekly_run()
            if weekly.get("status") != "awaiting_approval":
                return CaseResult(
                    id=case_id,
                    status="fail",
                    message=f"weekly status {weekly.get('status')}",
                    actual=weekly,
                )
            return CaseResult(
                id=case_id,
                status="pass",
                metric={
                    "degraded": bool(weekly.get("degraded")),
                    "tickets": len(ids),
                    "run_id": weekly.get("run_id"),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return CaseResult(id=case_id, status="fail", message=str(exc))
        finally:
            if client is not None:
                client.close()
            _stop_proc(proc)


def run(
    *,
    backend: str = "cues",
    seed: int = 42,
    suite: str | None = None,
    case: str | None = None,
    base_url: str | None = None,
    allow_remote: bool = False,
    keep_tmp: bool = False,
    skip_prod_hook: bool = False,
) -> int:
    started = datetime.now(tz=IST)
    mode: Literal["ci", "graded"] = "graded" if backend == "groq" else "ci"
    model = os.environ.get("LLM_MODEL") or (
        "openai/gpt-oss-120b" if backend == "groq" else "cues_only"
    )

    hook_result: CaseResult | None = None
    if not skip_prod_hook and not allow_remote:
        hook_result = _assert_production_ignores_faults()

    tmp_cm: tempfile.TemporaryDirectory[str] | None = None
    proc: subprocess.Popen[bytes] | None = None
    client: EvalClient | None = None
    tmp: Path | None = None
    db_url = ""
    samples_dir: Path
    log_dir: Path

    try:
        if base_url:
            if not allow_remote:
                raise SystemExit("--base-url requires --allow-remote")
            token = os.environ.get("ADMIN_TOKEN") or "eval-remote"
            client = EvalClient(base_url, token, timeout=180)
            client.wait_health(timeout=60)
            tmp_cm = tempfile.TemporaryDirectory(prefix="eval-remote-")
            tmp = Path(tmp_cm.name)
            samples_dir = tmp / "samples"
            log_dir = tmp / "logs"
            samples_dir.mkdir()
            log_dir.mkdir()
            db_url = os.environ.get("DATABASE_URL", "")
        else:
            tmp_cm = tempfile.TemporaryDirectory(prefix="eval-harness-")
            tmp = Path(tmp_cm.name)
            if keep_tmp:
                print(f"Temp dir: {tmp}", file=sys.stderr)
            samples_dir = tmp / "samples"
            log_dir = tmp / "logs"
            samples_dir.mkdir()
            log_dir.mkdir()
            token = "eval-admin-token"
            db_url = f"sqlite:///{tmp / 'eval.sqlite3'}"
            env = _base_env(
                db_url=db_url,
                samples=samples_dir,
                token=token,
                backend=backend,
                faults="on",
            )
            port = _free_port()
            log_path = log_dir / "uvicorn.log"
            proc = _start_server(env, port, log_path)
            client = EvalClient(f"http://127.0.0.1:{port}", token, timeout=180)
            _wait_healthy(client, proc, log_path)

        assert client is not None
        assert tmp is not None

        ctx = EvalContext(
            client=client,
            db_url=db_url,
            tmp_dir=tmp,
            samples_dir=samples_dir,
            log_dir=log_dir,
            seed=seed,
            backend=backend,
            mode=mode,
        )

        suites: dict[str, Any] = {}
        case_filter = case
        want = suite or "all"

        # Early wave before Suite 1 (adversarial fixtures for GC waves).
        if want in {"all", "feasibility", "grounding"}:
            submit_wave(ctx, "early")

        if want in {"all", "feasibility"}:
            feas = run_feasibility(ctx, case_filter=case_filter)
            if case_filter is None or case_filter.upper() == "FEAS-13":
                if not allow_remote:
                    feas13 = _run_feas_13_isolated(backend)
                    feas.cases = [c for c in feas.cases if c.id != "FEAS-13"] + [feas13]
                    if any(c.status == "fail" for c in feas.cases):
                        feas.status = "fail"
                    elif all(c.status in {"pass", "skip"} for c in feas.cases):
                        feas.status = "pass"
            suites["feasibility"] = _suite_to_dict(feas)
        elif want in {"edit", "grounding", "quality"} and not ctx.weekly_run_id:
            # Suites that need seeded tickets + a weekly run.
            setup_feasibility(ctx)

        if want in {"all", "grounding"} and not ctx.mid_wave_ticket_ids:
            submit_wave(ctx, "mid")

        if want in {"all", "edit"}:
            if allow_remote and not db_url.startswith("sqlite:///"):
                suites["edit"] = {
                    "status": "skip",
                    "cases": [
                        {
                            "id": "EDIT-ALL",
                            "status": "skip",
                            "message": "Suite 2 skipped: no local DB (--allow-remote)",
                        }
                    ],
                }
            else:
                if not ctx.weekly_run_id:
                    setup_feasibility(ctx)
                # Suite 2 acts on a fresh run's 4 pending actions (eval §5.2); Suite 1's
                # extra runs (FEAS-06/08/09/11) have superseded earlier ones by design.
                prepare_edit_run(ctx)
                edit = run_edit_correctness(ctx, case_filter=case_filter)
                suites["edit"] = _suite_to_dict(edit)

        if want in {"all", "grounding"} and not ctx.late_wave_ticket_ids:
            submit_wave(ctx, "late")

        if want in {"all", "grounding"}:
            ground = run_grounding_compliance(ctx, case_filter=case_filter)
            suites["grounding"] = _suite_to_dict(ground)

        if want in {"all", "quality"}:
            if not ctx.weekly_run_id:
                setup_feasibility(ctx)
            quality = run_agent_quality(ctx, case_filter=case_filter)
            suites["quality"] = _suite_to_dict(quality)

        planted = load_planted_pii()
        terms = collect_search_terms(planted)
        sweep = sweep_all(
            db_url=db_url,
            log_dir=log_dir,
            samples_dir=samples_dir,
            report_paths=[],
            terms=terms,
            exclude_dirs=[ROOT / "evals" / "fixtures"],
        )
        pii_payload: dict[str, Any] = {
            "hits": len(sweep.hits),
            "locations_scanned": sweep.locations_scanned,
            "details": [{"location": h.location, "pii_type": h.pii_type} for h in sweep.hits],
        }
        if sweep.hits and "grounding" in suites:
            for c in suites["grounding"].get("cases") or []:
                if c.get("id") == "GC-10":
                    c["status"] = "fail"
                    c["message"] = f"final sweep {len(sweep.hits)} hits"
                    c["actual"] = pii_payload["details"]
            if any(c.get("status") == "fail" for c in (suites["grounding"].get("cases") or [])):
                suites["grounding"]["status"] = "fail"

        finished = datetime.now(tz=IST)
        gating = _gating_pass(suites) and len(sweep.hits) == 0
        if hook_result and hook_result.status == "fail":
            gating = False

        payload: dict[str, Any] = {
            "run": {
                "started_at": started.isoformat(),
                "duration_s": (finished - started).total_seconds(),
                "mode": mode,
                "backend": backend,
                "model": model,
                "seed": seed,
                "git_sha": _git_sha(),
                "corpus_hash": _corpus_sha(),
                "reviews_hash": _file_sha(ROOT / "data" / "normalized_reviews.json"),
                "app_env": "test",
                "prod_hook": _suite_to_dict(
                    SuiteResult(
                        name="hooks",
                        status=hook_result.status if hook_result else "skip",
                        cases=[hook_result] if hook_result else [],
                    )
                ),
            },
            "suites": suites,
            "pii_sweep": pii_payload,
            "gating_pass": gating,
        }

        json_path, md_path = write_results(RESULTS_DIR, payload)
        # Final pass: only the report files themselves (eval.md §6.4).
        from .sweep import sweep_path as _sweep_path

        report_hits = []
        for path in (json_path, md_path):
            report_hits.extend(_sweep_path(path, terms))
        if report_hits:
            stub = {
                "gating_pass": False,
                "error": "PII found in report files",
                "pii_sweep": {
                    "hits": len(report_hits),
                    "details": [
                        {"location": h.location, "pii_type": h.pii_type} for h in report_hits
                    ],
                },
            }
            json_path.write_text(json.dumps(stub, indent=2), encoding="utf-8")
            md_path.write_text(
                "# Evaluation results — FAILED\n\nPII found in report files. Stub only.\n",
                encoding="utf-8",
            )
            gating = False

        print(md_path.read_text(encoding="utf-8"))
        print(f"\nWrote {json_path} and {md_path}", file=sys.stderr)
        return 0 if gating else 1

    finally:
        if client is not None:
            client.close()
        _stop_proc(proc)
        if tmp_cm is not None and not keep_tmp:
            tmp_cm.cleanup()
        elif tmp_cm is not None and keep_tmp and tmp is not None:
            print(f"Kept temp dir: {tmp}", file=sys.stderr)
            # Detach so TemporaryDirectory does not delete on exit.
            tmp_cm._finalizer.detach()  # type: ignore[attr-defined]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The Support Desk evaluation harness")
    parser.add_argument(
        "--backend",
        choices=("cues", "cues_only", "groq", "gemini"),
        default="cues",
        help="LLM backend (default: cues / CuesOnly for CI)",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--suite",
        choices=("feasibility", "edit", "grounding", "quality", "all"),
        default=None,
        help="Run one suite (default: all)",
    )
    parser.add_argument("--case", default=None, help="Run one case id (e.g. GC-05)")
    parser.add_argument("--base-url", default=None, help="Remote app URL")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Required with --base-url; skips Suite 2 without local DB",
    )
    parser.add_argument("--keep-tmp", action="store_true")
    parser.add_argument(
        "--skip-prod-hook",
        action="store_true",
        help="Skip the production-mode fault-injection refusal check",
    )
    args = parser.parse_args(argv)
    backend = "cues" if args.backend == "cues_only" else args.backend
    return run(
        backend=backend,
        seed=args.seed,
        suite=args.suite,
        case=args.case,
        base_url=args.base_url,
        allow_remote=args.allow_remote,
        keep_tmp=args.keep_tmp,
        skip_prod_hook=args.skip_prod_hook,
    )


if __name__ == "__main__":
    raise SystemExit(main())
