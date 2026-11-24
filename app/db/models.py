"""SQLAlchemy tables (architecture §5.3)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class WeeklyRun(Base):
    __tablename__ = "weekly_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # cron|manual
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    themes: Mapped[list[ThemeSnapshot]] = relationship(back_populates="run")
    pulse: Mapped[Pulse | None] = relationship(back_populates="run", uselist=False)
    explainer: Mapped[FeeExplainer | None] = relationship(back_populates="run", uselist=False)


class ThemeSnapshot(Base):
    __tablename__ = "theme_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("weekly_runs.id"), nullable=False)
    theme_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_rating: Mapped[float] = mapped_column(Float, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    window_start: Mapped[date] = mapped_column(Date, nullable=False)
    window_end: Mapped[date] = mapped_column(Date, nullable=False)
    trend: Mapped[str] = mapped_column(String(16), nullable=False)
    weekly_counts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_top3: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_fee_theme: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    run: Mapped[WeeklyRun] = relationship(back_populates="themes")


class Pulse(Base):
    __tablename__ = "pulses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("weekly_runs.id"), unique=True, nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quotes: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    action_ideas: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    repeat_questions: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    run: Mapped[WeeklyRun] = relationship(back_populates="pulse")


class FeeExplainer(Base):
    __tablename__ = "fee_explainers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("weekly_runs.id"), unique=True, nullable=False)
    bullets: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    source_urls: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    last_checked: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    run: Mapped[WeeklyRun] = relationship(back_populates="explainer")


class CanonicalExplainer(Base):
    __tablename__ = "canonical_explainers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fee_explainer_id: Mapped[str] = mapped_column(ForeignKey("fee_explainers.id"), nullable=False)
    theme_id: Mapped[str] = mapped_column(String(64), nullable=False, default="fees_brokerage")
    covers: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    approved_action_id: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    question_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    pii_types_found: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    fingerprint: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str | None] = mapped_column(String(16))
    route_team: Mapped[str | None] = mapped_column(String(64))
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refinement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    drafts: Mapped[list[DraftVersion]] = relationship(back_populates="ticket")
    agent_outputs: Mapped[list[AgentOutput]] = relationship(back_populates="ticket")
    refinements: Mapped[list[Refinement]] = relationship(back_populates="ticket")


class AgentOutput(Base):
    __tablename__ = "agent_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    agent: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_raw_choice: Mapped[str | None] = mapped_column(Text)
    validated_choice: Mapped[str | None] = mapped_column(Text)
    written_output: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="agent_outputs")


class DraftVersion(Base):
    __tablename__ = "draft_versions"
    __table_args__ = (UniqueConstraint("ticket_id", "version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    citation_url: Mapped[str | None] = mapped_column(Text)
    last_checked: Mapped[str | None] = mapped_column(String(32))
    routing_note: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    disagreement: Mapped[str | None] = mapped_column(Text)
    reviewer_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    ticket: Mapped[Ticket] = relationship(back_populates="drafts")


class Refinement(Base):
    __tablename__ = "refinements"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    hint_redacted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    nodes_rerun: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ticket: Mapped[Ticket] = relationship(back_populates="refinements")


class QuestionCluster(Base):
    __tablename__ = "question_clusters"

    fingerprint: Mapped[str] = mapped_column(String(128), primary_key=True)
    representative_text: Mapped[str] = mapped_column(Text, nullable=False)
    section: Mapped[str | None] = mapped_column(String(64))
    scheme_id: Mapped[str | None] = mapped_column(String(64))
    theme_id: Mapped[str | None] = mapped_column(String(64))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class OutboxReply(Base):
    __tablename__ = "outbox_replies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    draft_version_id: Mapped[str] = mapped_column(ForeignKey("draft_versions.id"), nullable=False)
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
