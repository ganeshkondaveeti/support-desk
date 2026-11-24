"""Canonical fee-explainer promotion and lookup (architecture §9.2)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.corpus.loader import Corpus, load_corpus
from app.db import models
from app.db.repository import add
from app.ids import safe_hex_id

logger = logging.getLogger(__name__)

# Mirrored from Facts agent (arch §8.4 / §9.2). ELSS is never in a family.
EQUITY_FAMILY: tuple[str, ...] = ("large_cap", "mid_cap", "small_cap")
FOF_FAMILY: tuple[str, ...] = ("gold_fof", "silver_fof")
FAMILY_BY_ID: dict[str, str] = {
    **{sid: "equity" for sid in EQUITY_FAMILY},
    **{sid: "fof" for sid in FOF_FAMILY},
}
FAMILIES: dict[str, tuple[str, ...]] = {
    "equity": EQUITY_FAMILY,
    "fof": FOF_FAMILY,
}


@dataclass(frozen=True, slots=True)
class PromoteResult:
    status: str  # promoted | pending | skipped | noop
    detail: str = ""
    canonical_id: str | None = None


def promote(
    session: Session,
    explainer_id: str,
    action_id: str,
) -> PromoteResult:
    """
    Promote a fee explainer to the canonical registry after its doc_append executes.

    - Derives ``covers`` from bullets, then expands to scheme families by identical
      normalized corpus text (arch §9.2).
    - Deactivates the previous active canonical for the theme.
    - Promotes only if this explainer's run ``as_of`` ≥ the active one's (G8).
    - Idempotent on ``(fee_explainer_id, approved_action_id)``.
    """
    explainer = session.get(models.FeeExplainer, explainer_id)
    if explainer is None:
        return PromoteResult(status="noop", detail="fee explainer not found")

    # Idempotent: same action already promoted this explainer.
    existing = session.scalars(
        select(models.CanonicalExplainer).where(
            models.CanonicalExplainer.fee_explainer_id == explainer_id,
            models.CanonicalExplainer.approved_action_id == action_id,
        )
    ).first()
    if existing is not None:
        return PromoteResult(
            status="promoted",
            detail="already promoted",
            canonical_id=existing.id,
        )

    run = session.get(models.WeeklyRun, explainer.run_id)
    if run is None:
        return PromoteResult(status="noop", detail="weekly run not found")

    active = _active_canonical(session, theme_id="fees_brokerage")
    if active is not None:
        active_explainer = session.get(models.FeeExplainer, active.fee_explainer_id)
        active_run = (
            session.get(models.WeeklyRun, active_explainer.run_id)
            if active_explainer is not None
            else None
        )
        if active_run is not None and run.as_of < active_run.as_of:
            return PromoteResult(
                status="skipped",
                detail=f"older than active {active.id}",
                canonical_id=active.id,
            )

    covers = derive_covers(list(explainer.bullets or []))
    now = datetime.now(UTC)
    if active is not None:
        active.active = False
        active.superseded_at = now

    canonical_id = safe_hex_id("ce")
    row = models.CanonicalExplainer(
        id=canonical_id,
        fee_explainer_id=explainer_id,
        theme_id="fees_brokerage",
        covers=covers,
        approved_action_id=action_id,
        active=True,
        activated_at=now,
        superseded_at=None,
    )
    add(session, row)
    session.flush()
    logger.info(
        "canonical promoted id=%s explainer=%s covers=%s",
        canonical_id,
        explainer_id,
        len(covers),
    )
    return PromoteResult(status="promoted", detail="ok", canonical_id=canonical_id)


def lookup(
    theme_id: str,
    section: str | None,
    scheme_id: str | None,
    *,
    session: Session | None = None,
) -> dict[str, Any] | None:
    """
    Find the active canonical explainer covering ``(scheme, section)``.

    Scheme may match via family expansion. Returns matching bullets (≤2) plus
    the bullet's ``source_url`` for citation.
    """
    if theme_id != "fees_brokerage" or not section:
        return None

    def _run(sess: Session) -> dict[str, Any] | None:
        active = _active_canonical(sess, theme_id=theme_id)
        if active is None:
            return None
        covers = list(active.covers or [])
        if not _covers_match(covers, scheme_id=scheme_id, section=section):
            return None
        explainer = sess.get(models.FeeExplainer, active.fee_explainer_id)
        if explainer is None:
            return None
        bullets = [
            b
            for b in (explainer.bullets or [])
            if isinstance(b, dict) and str(b.get("section") or "") == section
        ]
        if scheme_id:
            preferred = [
                b
                for b in bullets
                if str(b.get("scheme_id") or "") == scheme_id
                or _same_family(str(b.get("scheme_id") or ""), scheme_id)
            ]
            if preferred:
                bullets = preferred
        if not bullets:
            # Section covered via family expansion but no bullet for that section
            # on the explainer (shouldn't happen if covers was derived from bullets).
            bullets = [b for b in (explainer.bullets or []) if isinstance(b, dict)][:2]
        chosen = bullets[:2]
        if not chosen:
            return None
        first = chosen[0]
        family_name = FAMILY_BY_ID.get(scheme_id or "") if scheme_id else None
        family_schemes = list(FAMILIES.get(family_name or "", ())) if family_name else []
        return {
            "canonical_id": active.id,
            "fee_explainer_id": active.fee_explainer_id,
            "bullets": [str(b.get("text") or "") for b in chosen if b.get("text")],
            "source_url": first.get("source_url"),
            "citation_url": first.get("source_url"),
            "last_checked": first.get("last_checked") or explainer.last_checked,
            "section": section,
            "scheme_id": first.get("scheme_id"),
            "asked_scheme_id": scheme_id,
            "family": family_name,
            "family_schemes": family_schemes,
            "covers": covers,
        }

    if session is not None:
        return _run(session)

    from app.db.session import session_scope

    try:
        with session_scope() as sess:
            return _run(sess)
    except Exception:  # noqa: BLE001
        return None


def record_reuse(
    session: Session,
    *,
    canonical_id: str,
    ticket_id: str,
    draft_origin: str = "canonical",
) -> None:
    """Append a reuse-log audit event for the Canonical Explainers view."""
    add(
        session,
        models.AuditEvent(
            id=safe_hex_id("ae"),
            ts=datetime.now(UTC),
            actor="orchestrator",
            event="canonical_reuse",
            entity_type="canonical_explainer",
            entity_id=canonical_id,
            details={
                "ticket_id": ticket_id,
                "origin": draft_origin,
            },
        ),
    )
    session.flush()


def reuse_log(session: Session, canonical_id: str | None = None) -> list[dict[str, Any]]:
    """Return reuse audit events, optionally filtered to one canonical explainer."""
    stmt = select(models.AuditEvent).where(
        models.AuditEvent.event == "canonical_reuse",
        models.AuditEvent.entity_type == "canonical_explainer",
    )
    if canonical_id is not None:
        stmt = stmt.where(models.AuditEvent.entity_id == canonical_id)
    stmt = stmt.order_by(models.AuditEvent.ts.asc())
    rows = list(session.scalars(stmt).all())
    return [
        {
            "canonical_id": r.entity_id,
            "ts": r.ts.isoformat(),
            "ticket_id": (r.details or {}).get("ticket_id"),
            "details": dict(r.details or {}),
        }
        for r in rows
    ]


def reconcile_pending_promotions(session: Session) -> list[PromoteResult]:
    """
    Retry promotions recorded as ``result.promotion == "pending"``.

    Never re-runs MCP (G8). Idempotent via ``promote``.
    """
    actions = list(
        session.scalars(
            select(models.ProposedAction).where(
                models.ProposedAction.kind == "doc_append",
                models.ProposedAction.subject_type == "fee_explainer",
                models.ProposedAction.status == "executed",
            )
        ).all()
    )
    results: list[PromoteResult] = []
    for action in actions:
        result = action.result or {}
        if result.get("promotion") != "pending":
            continue
        outcome = promote(session, action.subject_id, action.id)
        merged = dict(result)
        if outcome.status == "skipped":
            merged["promotion"] = f"skipped: {outcome.detail}"
        else:
            merged["promotion"] = outcome.status
        merged["promotion_detail"] = outcome.detail
        if outcome.canonical_id:
            merged["canonical_id"] = outcome.canonical_id
        merged.pop("promotion_error", None)
        action.result = merged
        results.append(outcome)
    session.flush()
    return results


def derive_covers(
    bullets: list[Any],
    *,
    corpus: Corpus | None = None,
) -> list[list[str]]:
    """
    ``covers`` as a JSON list of ``[scheme_id, section]`` pairs.

    Starts from each bullet's ``(scheme_id, section)``, then expands to family
    members whose resolved corpus text is identical (ignoring trailing period).
    """
    facts = corpus or load_corpus()
    pairs: list[list[str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(scheme_id: str, section: str) -> None:
        key = (scheme_id, section)
        if key in seen:
            return
        seen.add(key)
        pairs.append([scheme_id, section])

    for bullet in bullets:
        if not isinstance(bullet, dict):
            continue
        scheme_id = str(bullet.get("scheme_id") or "")
        section = str(bullet.get("section") or "")
        if not scheme_id or not section:
            continue
        _add(scheme_id, section)
        for member in _family_members_with_identical_text(facts, scheme_id, section):
            _add(member, section)
    return pairs


def _family_members_with_identical_text(
    corpus: Corpus,
    scheme_id: str,
    section: str,
) -> list[str]:
    family = FAMILY_BY_ID.get(scheme_id)
    if family is None:
        return []
    members = FAMILIES.get(family, ())
    lead = corpus.get(scheme_id, section)
    if lead is None:
        return []
    lead_norm = _norm_block(lead.text)
    out: list[str] = []
    for member in members:
        if member == scheme_id:
            continue
        block = corpus.get(member, section)
        if block is None:
            continue
        if _norm_block(block.text) == lead_norm:
            out.append(member)
    return out


def _covers_match(
    covers: list[Any],
    *,
    scheme_id: str | None,
    section: str,
) -> bool:
    for item in covers:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            cov_scheme, cov_section = str(item[0]), str(item[1])
        elif isinstance(item, dict):
            cov_scheme = str(item.get("scheme_id") or "")
            cov_section = str(item.get("section") or "")
        else:
            continue
        if cov_section != section:
            continue
        if scheme_id is None or cov_scheme == scheme_id:
            return True
        if _same_family(cov_scheme, scheme_id):
            return True
    return False


def _same_family(a: str, b: str) -> bool:
    fa = FAMILY_BY_ID.get(a)
    fb = FAMILY_BY_ID.get(b)
    return fa is not None and fa == fb


def _active_canonical(
    session: Session,
    *,
    theme_id: str = "fees_brokerage",
) -> models.CanonicalExplainer | None:
    return session.scalars(
        select(models.CanonicalExplainer).where(
            models.CanonicalExplainer.theme_id == theme_id,
            models.CanonicalExplainer.active.is_(True),
        )
    ).first()


def _norm_block(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".")).casefold()
