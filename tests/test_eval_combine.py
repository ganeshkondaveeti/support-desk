"""Split graded runs (one --suite per run) gate and merge correctly (eval.md §9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals.combine import combine
from evals.run_all import _gating_pass

RUN = {
    "mode": "graded",
    "backend": "groq",
    "model": "openai/gpt-oss-120b",
    "seed": 42,
    "corpus_hash": "sha256:c",
    "reviews_hash": "sha256:r",
    "git_sha": "abc",
    "duration_s": 10,
}


def _suite(status: str = "pass", case_status: str = "pass") -> dict:
    return {"status": status, "cases": [{"id": "X-01", "status": case_status}]}


def _write(
    tmp: Path, name: str, suites: dict, *, hits: int = 0, started: str = "2026-11-25T12:00:00+05:30"
) -> Path:
    path = tmp / name
    payload = {
        "run": {**RUN, "started_at": started},
        "suites": suites,
        "pii_sweep": {"hits": hits, "locations_scanned": 6, "details": []},
    }
    path.write_text(json.dumps(payload))
    return path


def test_single_suite_run_is_judged_on_that_suite_only() -> None:
    assert _gating_pass({"edit": _suite()})
    assert not _gating_pass({"edit": _suite(case_status="fail")})
    assert not _gating_pass({"quality": _suite()})  # no gating suite ran


def test_combine_merges_suites_and_gates_on_all_three(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.json", {"edit": _suite()}, started="2026-11-25T12:00:00+05:30")
    b = _write(tmp_path, "b.json", {"feasibility": _suite()}, started="2026-11-26T12:00:00+05:30")
    partial = combine([a, b])
    assert set(partial["suites"]) == {"edit", "feasibility"}
    assert partial["gating_pass"] is False  # grounding missing
    c = _write(tmp_path, "c.json", {"grounding": _suite(), "quality": _suite("info", "info")})
    full = combine([a, b, c])
    assert full["gating_pass"] is True
    assert full["run"]["started_at"] == "2026-11-25T12:00:00+05:30"
    assert full["pii_sweep"]["locations_scanned"] == 18


def test_combine_fails_on_pii_hits_duplicates_and_mismatched_runs(tmp_path: Path) -> None:
    a = _write(tmp_path, "a.json", {"edit": _suite(), "feasibility": _suite()})
    b = _write(tmp_path, "b.json", {"grounding": _suite()}, hits=1)
    assert combine([a, b])["gating_pass"] is False
    with pytest.raises(ValueError, match="both"):
        combine([a, _write(tmp_path, "dup.json", {"edit": _suite()})])
    other = json.loads(b.read_text())
    other["run"]["seed"] = 7
    b.write_text(json.dumps(other))
    with pytest.raises(ValueError, match="seed"):
        combine([a, b])


def test_llm_down_also_skips_the_pulse_action_ideas_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """FEAS-13 passed in graded mode with real Groq ideas: llm_down didn't cover the pulse."""
    from app.config import clear_settings_cache
    from app.weekly import pulse

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EVAL_FAULTS", "llm_down")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    clear_settings_cache()

    def no_groq(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("llm_down must not reach Groq")

    monkeypatch.setattr("groq.Groq", no_groq)
    try:
        assert pulse.default_llm_action_ideas([], [], []) is None
    finally:
        clear_settings_cache()


def test_notes_reach_the_json_and_latest_md(tmp_path: Path) -> None:
    from evals.report import render_latest_md

    a = _write(tmp_path, "a.json", {"feasibility": _suite()})
    payload = combine([a], notes=["FEAS-13 re-checked after the fix.", "  "])
    assert payload["run"]["notes"] == ["FEAS-13 re-checked after the fix."]
    md = render_latest_md(payload)
    assert "**Notes**\n\n- FEAS-13 re-checked after the fix.\n" in md
    assert "**Notes**" not in render_latest_md(combine([a]))


def test_the_cli_never_overwrites_an_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.combine as combine_mod

    monkeypatch.setattr(combine_mod, "RESULTS_DIR", tmp_path)
    a = _write(tmp_path, "20261125-120000.json", {"edit": _suite()})
    b = _write(
        tmp_path,
        "20261126-090000.json",
        {"feasibility": _suite()},
        started="2026-11-26T09:00:00+05:30",
    )
    before = {p: p.read_text() for p in (a, b)}
    combine_mod.main([str(a), str(b), "--note", "interim"])
    assert {p: p.read_text() for p in (a, b)} == before
    combined = list(tmp_path.glob("combined-*.json"))
    assert len(combined) == 1
    assert set(json.loads(combined[0].read_text())["suites"]) == {"edit", "feasibility"}
    assert (tmp_path / "latest.md").exists()
