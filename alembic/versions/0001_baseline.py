"""Baseline schema for architecture §5.3 tables.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_runs",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_table(
        "theme_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("weekly_runs.id"), nullable=False),
        sa.Column("theme_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("review_ids", sa.JSON(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("avg_rating", sa.Float(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False),
        sa.Column("trend", sa.String(length=16), nullable=False),
        sa.Column("weekly_counts", sa.JSON(), nullable=False),
        sa.Column("is_top3", sa.Boolean(), nullable=False),
        sa.Column("is_fee_theme", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "pulses",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "run_id", sa.String(length=64), sa.ForeignKey("weekly_runs.id"), nullable=False, unique=True
        ),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("quotes", sa.JSON(), nullable=False),
        sa.Column("action_ideas", sa.JSON(), nullable=False),
        sa.Column("repeat_questions", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "fee_explainers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "run_id", sa.String(length=64), sa.ForeignKey("weekly_runs.id"), nullable=False, unique=True
        ),
        sa.Column("bullets", sa.JSON(), nullable=False),
        sa.Column("source_urls", sa.JSON(), nullable=False),
        sa.Column("last_checked", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
    )
    op.create_table(
        "canonical_explainers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column(
            "fee_explainer_id",
            sa.String(length=64),
            sa.ForeignKey("fee_explainers.id"),
            nullable=False,
        ),
        sa.Column("theme_id", sa.String(length=64), nullable=False),
        sa.Column("covers", sa.JSON(), nullable=False),
        sa.Column("approved_action_id", sa.String(length=64)),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("question_redacted", sa.Text(), nullable=False),
        sa.Column("pii_types_found", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=16)),
        sa.Column("route_team", sa.String(length=64)),
        sa.Column("rework_count", sa.Integer(), nullable=False),
        sa.Column("refinement_count", sa.Integer(), nullable=False),
    )
    op.create_table(
        "agent_outputs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticket_id", sa.String(length=64), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("agent", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("model_raw_choice", sa.Text()),
        sa.Column("validated_choice", sa.Text()),
        sa.Column("written_output", sa.JSON(), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "draft_versions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("ticket_id", sa.String(length=64), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("citation_url", sa.Text()),
        sa.Column("last_checked", sa.String(length=32)),
        sa.Column("routing_note", sa.JSON(), nullable=False),
        sa.Column("disagreement", sa.Text()),
        sa.Column("reviewer_result", sa.JSON(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("ticket_id", "version"),
    )
    op.create_table(
        "refinements",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("ticket_id", sa.String(length=64), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("hint_redacted", sa.Text(), nullable=False),
        sa.Column("nodes_rerun", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "question_clusters",
        sa.Column("fingerprint", sa.String(length=128), primary_key=True),
        sa.Column("representative_text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(length=64)),
        sa.Column("scheme_id", sa.String(length=64)),
        sa.Column("theme_id", sa.String(length=64)),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "proposed_actions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decided_by", sa.String(length=64)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
        sa.Column("result", sa.JSON()),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_table(
        "outbox_replies",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("ticket_id", sa.String(length=64), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column(
            "draft_version_id",
            sa.String(length=64),
            sa.ForeignKey("draft_versions.id"),
            nullable=False,
        ),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "outbox_replies",
        "audit_events",
        "proposed_actions",
        "question_clusters",
        "refinements",
        "draft_versions",
        "agent_outputs",
        "tickets",
        "canonical_explainers",
        "fee_explainers",
        "pulses",
        "theme_snapshots",
        "weekly_runs",
    ):
        op.drop_table(table)
