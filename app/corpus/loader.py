"""Load snapshots A + B into a resolved Corpus (architecture §5.1)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from app.corpus.normalize import CANONICAL_SECTION_LIST, normalize_b_scheme
from app.corpus.quarantine import assert_no_quarantine
from app.guardrails.allowlists import register_corpus

SnapshotId = Literal["A", "B"]

B_FILE_TO_SCHEME: dict[str, str] = {
    "hdfc_large_cap_fund": "large_cap",
    "hdfc_mid_cap_fund": "mid_cap",
    "hdfc_small_cap_fund": "small_cap",
    "hdfc_gold_etf_fof": "gold_fof",
    "hdfc_elss_tax_saver_fund": "elss_tax_saver",
}

EXPECTED_SCHEME_IDS: frozenset[str] = frozenset(
    {
        "large_cap",
        "mid_cap",
        "small_cap",
        "gold_fof",
        "silver_fof",
        "elss_tax_saver",
    }
)

A_LAST_CHECKED = "28-Jun-2026"
B_LAST_CHECKED = "01-Sep-2026"
A_NAV_DATE = "25-Jun-2026"
B_NAV_DATE = "31-Aug-2026"


@dataclass(frozen=True, slots=True)
class Block:
    scheme_id: str
    scheme_name: str
    section: str
    text: str
    source_url: str
    snapshot: SnapshotId
    last_checked: str
    nav_date: str
    origin: dict[str, str]


@dataclass(frozen=True, slots=True)
class Corpus:
    """Immutable facts corpus over snapshots A + B."""

    blocks: dict[tuple[str, str], Block]
    all_blocks: dict[tuple[str, str, SnapshotId], Block]
    scheme_names: dict[str, str]
    quarantined: tuple[str, ...] = ()

    @property
    def scheme_ids(self) -> frozenset[str]:
        return frozenset(sid for sid, _ in self.blocks)

    @property
    def citation_urls(self) -> frozenset[str]:
        return frozenset(b.source_url for b in self.blocks.values())

    def get(self, scheme_id: str, section: str) -> Block | None:
        return self.blocks.get((scheme_id, section))


class CorpusValidationError(RuntimeError):
    """Raised when startup corpus validation fails."""


def data_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "data" / "parsed" / "parse_manifest.json"
        if candidate.is_file():
            return candidate.parents[1]
    raise FileNotFoundError("data/parsed/parse_manifest.json not found")


@lru_cache(maxsize=1)
def load_corpus(root: Path | None = None) -> Corpus:
    """Load, normalize, validate, and cache the corpus. Refuses bad data."""
    base = Path(root) if root is not None else data_dir()
    corpus = _build(base)
    validate_corpus(corpus, base)
    register_corpus(
        scheme_ids=sorted(corpus.scheme_ids),
        sections=list(CANONICAL_SECTION_LIST),
        citation_urls=sorted(corpus.citation_urls),
    )
    return corpus


def reload_corpus(root: Path | None = None) -> Corpus:
    load_corpus.cache_clear()
    return load_corpus(root)


def validate_corpus(corpus: Corpus, base: Path | None = None) -> None:
    """Architecture §5.1 startup validation list."""
    base = base or data_dir()
    parsed = base / "parsed"
    processed = base / "processed"

    manifest = _read_json(parsed / "parse_manifest.json")
    if manifest.get("scheme_count") != 5:
        raise CorpusValidationError(f"A scheme_count must be 5, got {manifest.get('scheme_count')}")

    a_files = list(parsed.glob("*.json"))
    a_scheme_files = [p for p in a_files if p.name != "parse_manifest.json"]
    if len(a_scheme_files) != 5:
        raise CorpusValidationError(f"A must have 5 scheme files, got {len(a_scheme_files)}")
    for path in a_scheme_files:
        payload = _read_json(path)
        blocks = payload.get("text_blocks") or []
        if len(blocks) != 7:
            raise CorpusValidationError(f"{path.name} must have 7 blocks, got {len(blocks)}")

    b_files = sorted(processed.glob("hdfc_*.json"))
    if len(b_files) != 5:
        raise CorpusValidationError(f"B must have 5 files, got {len(b_files)}")
    for path in b_files:
        stem = path.stem
        if stem not in B_FILE_TO_SCHEME:
            raise CorpusValidationError(f"unknown B file: {path.name}")
        payload = _read_json(path)
        name = str(payload.get("scheme_name") or "")
        if not name:
            raise CorpusValidationError(f"{path.name} missing scheme_name")

    if corpus.scheme_ids != EXPECTED_SCHEME_IDS:
        raise CorpusValidationError(f"union must be 6 schemes, got {sorted(corpus.scheme_ids)}")

    # URL checks
    urls_by_scheme: dict[str, set[str]] = {}
    for (scheme_id, _section, _snap), block in corpus.all_blocks.items():
        if "groww.in" not in block.source_url:
            raise CorpusValidationError(f"non-groww URL: {block.source_url}")
        urls_by_scheme.setdefault(scheme_id, set()).add(block.source_url)
    for scheme_id, urls in urls_by_scheme.items():
        if len(urls) != 1:
            raise CorpusValidationError(f"{scheme_id} has mismatched URLs: {urls}")

    # Resolved keys unique (dict guarantees) + no quarantine residue
    for block in corpus.blocks.values():
        assert_no_quarantine(block.text)
    for block in corpus.all_blocks.values():
        assert_no_quarantine(block.text)


def _build(base: Path) -> Corpus:
    all_blocks: dict[tuple[str, str, SnapshotId], Block] = {}
    scheme_names: dict[str, str] = {}
    quarantined: list[str] = []

    # --- Snapshot A ---
    parsed = base / "parsed"
    for path in sorted(parsed.glob("*.json")):
        if path.name == "parse_manifest.json":
            continue
        payload = _read_json(path)
        scheme_id = str(payload["scheme_id"])
        scheme_name = str(payload["scheme_name"])
        source_url = str(payload["source_url"])
        nav_date = str(payload.get("nav_date") or A_NAV_DATE)
        last_checked = _last_checked_a(payload.get("fetched_at"))
        scheme_names[scheme_id] = scheme_name
        for block in payload.get("text_blocks") or []:
            section = str(block["section_title"])
            text = str(block.get("text") or "").strip()
            if section not in CANONICAL_SECTION_LIST:
                continue
            b = Block(
                scheme_id=scheme_id,
                scheme_name=scheme_name,
                section=section,
                text=text if text.endswith((".", "!", "?")) else text + ("." if text else ""),
                source_url=source_url,
                snapshot="A",
                last_checked=last_checked,
                nav_date=nav_date,
                origin={"file": path.name, "section_title": section},
            )
            all_blocks[(scheme_id, section, "A")] = b

    # --- Snapshot B ---
    processed = base / "processed"
    for path in sorted(processed.glob("hdfc_*.json")):
        payload = _read_json(path)
        scheme_id = B_FILE_TO_SCHEME[path.stem]
        scheme_name = str(payload.get("scheme_name") or scheme_id)
        # Prefer A's fuller name when available.
        scheme_name = scheme_names.get(scheme_id, scheme_name)
        scheme_names[scheme_id] = scheme_name
        source_url = str(payload["source_url"])
        last_checked = _last_checked_b(payload.get("scrape_date"))
        nav_date = B_NAV_DATE
        normalized = normalize_b_scheme(payload)
        for section, text in normalized.items():
            b = Block(
                scheme_id=scheme_id,
                scheme_name=scheme_name,
                section=section,
                text=text,
                source_url=source_url,
                snapshot="B",
                last_checked=last_checked,
                nav_date=nav_date,
                origin={"file": path.name, "section_name": section},
            )
            all_blocks[(scheme_id, section, "B")] = b

    # Resolve: B wins when present, else A.
    blocks: dict[tuple[str, str], Block] = {}
    for scheme_id in EXPECTED_SCHEME_IDS:
        for section in CANONICAL_SECTION_LIST:
            b_block = all_blocks.get((scheme_id, section, "B"))
            a_block = all_blocks.get((scheme_id, section, "A"))
            chosen = b_block or a_block
            if chosen is not None:
                blocks[(scheme_id, section)] = chosen

    return Corpus(
        blocks=blocks,
        all_blocks=all_blocks,
        scheme_names=scheme_names,
        quarantined=tuple(quarantined),
    )


def _last_checked_a(fetched_at: Any) -> str:
    if not fetched_at:
        return A_LAST_CHECKED
    try:
        dt = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        return _fmt_date(dt.date())
    except ValueError:
        return A_LAST_CHECKED


def _last_checked_b(scrape_date: Any) -> str:
    if not scrape_date:
        return B_LAST_CHECKED
    try:
        d = date.fromisoformat(str(scrape_date)[:10])
        return _fmt_date(d)
    except ValueError:
        return B_LAST_CHECKED


def _fmt_date(d: date) -> str:
    months = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    return f"{d.day:02d}-{months[d.month - 1]}-{d.year}"


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload
