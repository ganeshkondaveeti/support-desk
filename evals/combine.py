"""Merge split eval runs (one ``--suite`` per run) into one report (eval.md §9).

Groq's free tier can't fit a full graded run in one day, so the graded run is
split by suite across days. This merges those results JSONs into one combined
``{timestamp}.json`` + ``latest.md``:

    python -m evals.combine evals/results/A.json evals/results/B.json ...

Each suite is taken from the file that ran it; a suite in two files is an error.
PII sweep hits and locations are summed. The combined run is gating only if every
gating suite is present and passes, and the sweep found nothing. ``--note`` adds
a line to the report (e.g. a case re-checked after its suite ran).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .report import write_results
from .run_all import GATING_SUITES, RESULTS_DIR, _gating_pass


def combine(paths: list[Path], notes: list[str] | None = None) -> dict[str, Any]:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    if not runs:
        raise ValueError("no results files given")
    first = runs[0]["run"]
    for path, payload in zip(paths, runs, strict=True):
        run = payload["run"]
        for key in ("mode", "backend", "model", "seed", "corpus_hash", "reviews_hash"):
            if run.get(key) != first.get(key):
                raise ValueError(
                    f"{path.name}: {key}={run.get(key)!r} differs from {first.get(key)!r}"
                )

    suites: dict[str, Any] = {}
    sources: dict[str, str] = {}
    hits = 0
    locations = 0
    details: list[Any] = []
    for path, payload in zip(paths, runs, strict=True):
        for name, suite in (payload.get("suites") or {}).items():
            if name in suites:
                raise ValueError(f"suite {name!r} is in both {sources[name]} and {path.name}")
            suites[name] = suite
            sources[name] = path.name
        sweep = payload.get("pii_sweep") or {}
        hits += int(sweep.get("hits") or 0)
        locations += int(sweep.get("locations_scanned") or 0)
        details.extend(sweep.get("details") or [])

    started = min(p["run"]["started_at"] for p in runs)
    hook = next(
        (p["run"].get("prod_hook") for p in runs if (p["run"].get("prod_hook") or {}).get("cases")),
        None,
    )
    hook_ok = hook is None or hook.get("status") != "fail"
    missing = [name for name in GATING_SUITES if name not in suites]
    return {
        "run": {
            **first,
            "started_at": started,
            "duration_s": sum(float(p["run"].get("duration_s") or 0) for p in runs),
            "git_sha": ",".join(dict.fromkeys(p["run"].get("git_sha", "") for p in runs)),
            "prod_hook": hook or first.get("prod_hook"),
            "combined_from": sources,
            "notes": [n.strip() for n in notes or [] if n.strip()],
        },
        "suites": suites,
        "pii_sweep": {"hits": hits, "locations_scanned": locations, "details": details},
        "gating_pass": not missing and _gating_pass(suites) and hits == 0 and hook_ok,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("results", nargs="+", type=Path, help="results JSON files, one per suite")
    parser.add_argument(
        "--note", action="append", default=[], help="a line for the report; repeatable"
    )
    args = parser.parse_args(argv)
    payload = combine(args.results, notes=args.note)
    json_path, md_path = write_results(RESULTS_DIR, payload)
    print(f"wrote {json_path} and {md_path} (gating_pass={payload['gating_pass']})")
    return 0 if payload["gating_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
