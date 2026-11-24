"""Pydantic request/response models for the HTTP API (architecture §12)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Free-text fields stay plain ``str`` (no max_length). Length/empty checks run
# after ``pii.redact()`` in handlers (G6 / arch §12).
MAX_FREE_TEXT_CHARS = 2000


class WeeklyRunSummary(BaseModel):
    id: str
    as_of: date
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    noop: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    corpus_loaded: bool
    db_reachable: bool
    last_weekly_run: WeeklyRunSummary | None = None
    pending_approvals: int = 0
    gate_mode: Literal["full", "read_only"] = "full"
    seeded: bool = False
    # P9.5: header dates and the UI's link allow-list (arch §12, §13).
    corpus_last_checked: dict[str, str] = Field(default_factory=dict)
    public_domains: list[str] = Field(default_factory=list)
    edu_urls: list[str] = Field(default_factory=list)


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    channel: str = "api"


class TicketCreateResponse(BaseModel):
    id: str
    status: str
    pii_types_found: list[str] = Field(default_factory=list)


class TicketSeedResponse(BaseModel):
    job_id: str
    enqueued: int
    status: Literal["queued"] = "queued"


class TicketListItem(BaseModel):
    id: str
    received_at: datetime
    channel: str
    question_redacted: str
    status: str
    priority: str | None = None
    route_team: str | None = None
    pii_types_found: list[str] = Field(default_factory=list)
    refinement_count: int = 0
    rework_count: int = 0
    fingerprint: str | None = None


class TicketListResponse(BaseModel):
    items: list[TicketListItem]
    total: int


class DraftVersionOut(BaseModel):
    id: str
    version: int
    reply_text: str
    citation_url: str | None = None
    last_checked: str | None = None
    routing_note: dict[str, Any] = Field(default_factory=dict)
    disagreement: str | None = None
    reviewer_result: dict[str, Any] = Field(default_factory=dict)
    origin: str
    is_current: bool


class AgentOutputOut(BaseModel):
    id: int
    agent: str
    attempt: int
    model_raw_choice: str | None = None
    validated_choice: str | None = None
    written_output: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    created_at: datetime


class RefinementOut(BaseModel):
    id: str
    n: int
    kind: str
    hint_redacted: str
    nodes_rerun: list[str] = Field(default_factory=list)
    created_at: datetime


class TicketDetailResponse(BaseModel):
    id: str
    received_at: datetime
    channel: str
    question_redacted: str
    status: str
    priority: str | None = None
    route_team: str | None = None
    pii_types_found: list[str] = Field(default_factory=list)
    fingerprint: str | None = None
    rework_count: int = 0
    refinement_count: int = 0
    current_draft: DraftVersionOut | None = None
    drafts: list[DraftVersionOut] = Field(default_factory=list)
    agent_outputs: list[AgentOutputOut] = Field(default_factory=list)
    refinements: list[RefinementOut] = Field(default_factory=list)
    pending_reply_send_id: str | None = None


class TicketEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class TicketRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = ""


class TicketRefineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["shorter", "different_scheme", "check_fee_issue", "other"]
    hint: str = ""


class WeeklyRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = None
    reviews_path: str | None = None


class WeeklyRunResponse(BaseModel):
    run_id: str
    as_of: date
    status: str
    noop: bool = False
    degraded: bool = False
    theme_counts: dict[str, int] = Field(default_factory=dict)
    pulse_word_count: int = 0
    explainer_bullets: int = 0
    action_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class ThemeOut(BaseModel):
    theme_id: str
    name: str
    description: str = ""
    review_ids: list[Any] = Field(default_factory=list)
    count: int
    avg_rating: float
    score: float
    window_start: date
    window_end: date
    trend: str
    weekly_counts: dict[str, Any] = Field(default_factory=dict)
    is_top3: bool = False
    is_fee_theme: bool = False


class PulseOut(BaseModel):
    id: str
    body_md: str
    word_count: int
    quotes: list[Any] = Field(default_factory=list)
    action_ideas: list[Any] = Field(default_factory=list)
    repeat_questions: list[Any] = Field(default_factory=list)
    status: str


class ExplainerOut(BaseModel):
    id: str
    bullets: list[Any] = Field(default_factory=list)
    source_urls: list[Any] = Field(default_factory=list)
    last_checked: str
    status: str


class WeeklyRunDetail(BaseModel):
    id: str
    as_of: date
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    input_hash: str
    error: str | None = None
    themes: list[ThemeOut] = Field(default_factory=list)
    pulse: PulseOut | None = None
    explainer: ExplainerOut | None = None


class WeeklyRunListResponse(BaseModel):
    items: list[WeeklyRunSummary]
    total: int


class SourceAltBlock(BaseModel):
    snapshot: str
    text: str
    last_checked: str
    nav_date: str


class SourceBlockOut(BaseModel):
    scheme_id: str
    scheme_name: str
    section: str
    text: str
    source_url: str
    snapshot: str
    last_checked: str
    nav_date: str
    other_snapshot: SourceAltBlock | None = None
    citing_draft_ids: list[str] = Field(default_factory=list)
    citing_explainer_ids: list[str] = Field(default_factory=list)


class DriftRowOut(BaseModel):
    scheme_id: str
    expense_ratio_a: str | None = None
    expense_ratio_b: str | None = None
    fund_size_a: str | None = None
    fund_size_b: str | None = None
    riskometer_a: str | None = None
    riskometer_b: str | None = None
    exit_load: str


class SourcesResponse(BaseModel):
    blocks: list[SourceBlockOut]
    drift: list[DriftRowOut] = Field(default_factory=list)
    quarantined: list[str] = Field(default_factory=list)


class ActionOut(BaseModel):
    id: str
    kind: str
    subject_type: str
    subject_id: str
    target: dict[str, Any] = Field(default_factory=dict)
    payload: str
    payload_sha256: str
    status: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] | None = None
    what_else_happens: str = ""
    destination: str = ""


class ActionListResponse(BaseModel):
    items: list[ActionOut]
    total: int


class CanonicalExplainerOut(BaseModel):
    id: str
    fee_explainer_id: str
    theme_id: str
    covers: list[Any] = Field(default_factory=list)
    approved_action_id: str | None = None
    active: bool
    activated_at: datetime
    superseded_at: datetime | None = None
    bullets: list[Any] = Field(default_factory=list)
    source_urls: list[Any] = Field(default_factory=list)
    last_checked: str | None = None
    reuse_log: list[dict[str, Any]] = Field(default_factory=list)


class CanonicalExplainersResponse(BaseModel):
    active: CanonicalExplainerOut | None = None
    history: list[CanonicalExplainerOut] = Field(default_factory=list)


class AuditEventOut(BaseModel):
    id: str
    ts: datetime
    actor: str
    event: str
    entity_type: str
    entity_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    items: list[AuditEventOut]
    total: int
    offset: int = 0
    limit: int = 50


class ErrorBody(BaseModel):
    detail: str
    code: str | None = None


class ValidationErrorItem(BaseModel):
    loc: list[str | int]
    type: str
    msg: str


class ValidationErrorBody(BaseModel):
    detail: list[ValidationErrorItem]
