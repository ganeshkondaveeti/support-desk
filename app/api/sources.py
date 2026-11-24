"""GET /api/sources — corpus index (architecture §12–13)."""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import DriftRowOut, SourceAltBlock, SourceBlockOut, SourcesResponse
from app.corpus.drift import compute_drift
from app.corpus.loader import load_corpus
from app.db import models

router = APIRouter(tags=["sources"])


@router.get("/api/sources", response_model=SourcesResponse)
def get_sources(
    session: DbSession,
    scheme: str | None = None,
    section: str | None = None,
    snapshot: str | None = Query(default=None, pattern="^[AB]$"),
) -> SourcesResponse:
    corpus = load_corpus()
    citing_by_url = _citing_drafts(session)
    citing_explainers = _citing_explainers(session)

    blocks: list[SourceBlockOut] = []
    for (scheme_id, sec), block in sorted(corpus.blocks.items()):
        if scheme and scheme_id != scheme:
            continue
        if section and sec != section:
            continue
        if snapshot and block.snapshot != snapshot:
            continue

        other = None
        alt_snap = "A" if block.snapshot == "B" else "B"
        alt = corpus.all_blocks.get((scheme_id, sec, alt_snap))  # type: ignore[arg-type]
        if alt is not None:
            other = SourceAltBlock(
                snapshot=alt.snapshot,
                text=alt.text,
                last_checked=alt.last_checked,
                nav_date=alt.nav_date,
            )

        blocks.append(
            SourceBlockOut(
                scheme_id=scheme_id,
                scheme_name=block.scheme_name,
                section=sec,
                text=block.text,
                source_url=block.source_url,
                snapshot=block.snapshot,
                last_checked=block.last_checked,
                nav_date=block.nav_date,
                other_snapshot=other,
                citing_draft_ids=citing_by_url.get(block.source_url, []),
                citing_explainer_ids=citing_explainers.get(block.source_url, []),
            )
        )

    drift_rows = [
        DriftRowOut(
            scheme_id=r.scheme_id,
            expense_ratio_a=r.expense_ratio_a,
            expense_ratio_b=r.expense_ratio_b,
            fund_size_a=r.fund_size_a,
            fund_size_b=r.fund_size_b,
            riskometer_a=r.riskometer_a,
            riskometer_b=r.riskometer_b,
            exit_load=r.exit_load,
        )
        for r in compute_drift(corpus)
    ]
    return SourcesResponse(
        blocks=blocks,
        drift=drift_rows,
        quarantined=list(corpus.quarantined),
    )


def _citing_drafts(session: DbSession) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = session.scalars(
        select(models.DraftVersion).where(models.DraftVersion.citation_url.is_not(None))
    ).all()
    for d in rows:
        url = d.citation_url or ""
        if not url:
            continue
        out.setdefault(url, []).append(d.id)
    return out


def _citing_explainers(session: DbSession) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    rows = session.scalars(select(models.FeeExplainer)).all()
    for expl in rows:
        for url in expl.source_urls or []:
            if isinstance(url, str) and url:
                out.setdefault(url, []).append(expl.id)
        for bullet in expl.bullets or []:
            if not isinstance(bullet, dict):
                continue
            url = bullet.get("source_url")
            if isinstance(url, str) and url:
                out.setdefault(url, []).append(expl.id)
    # de-dupe
    return {k: sorted(set(v)) for k, v in out.items()}
