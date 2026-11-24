"""Shared datatypes for the evaluation harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .client import EvalClient


CaseStatus = Literal["pass", "fail", "skip", "info"]


@dataclass(slots=True)
class CaseResult:
    id: str
    status: CaseStatus
    metric: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    expected: Any = None
    actual: Any = None


@dataclass(slots=True)
class SuiteResult:
    name: str
    status: CaseStatus
    cases: list[CaseResult] = field(default_factory=list)


@dataclass
class EvalContext:
    """Mutable run context shared across suites in one harness session."""

    client: EvalClient
    db_url: str
    tmp_dir: Path
    samples_dir: Path
    log_dir: Path
    bindings: dict[str, str] = field(default_factory=dict)
    ticket_map: dict[str, str] = field(default_factory=dict)
    seed: int = 42
    backend: str = "cues"
    mode: Literal["ci", "graded"] = "ci"
    early_wave_ticket_ids: list[str] = field(default_factory=list)
    mid_wave_ticket_ids: list[str] = field(default_factory=list)
    late_wave_ticket_ids: list[str] = field(default_factory=list)
    weekly_run_id: str | None = None
    pulse_email_action_id: str | None = None
    pulse_doc_action_id: str | None = None
    expl_doc_action_id: str | None = None
    expl_email_action_id: str | None = None
    blocked_ticket_id: str | None = None
