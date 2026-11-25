"""PII literal sweep for GC-10 (eval.md §6.4)."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .fixtures_loader import ROOT as FIXTURES_ROOT
from .fixtures_loader import load_planted_pii

FIXTURES_EXCLUDE = FIXTURES_ROOT.resolve()


@dataclass(frozen=True, slots=True)
class Hit:
    location: str
    pii_type: str


@dataclass(slots=True)
class SweepResult:
    hits: list[Hit] = field(default_factory=list)
    locations_scanned: int = 0


def collect_search_terms(planted: dict[str, Any] | None = None) -> list[str]:
    """All planted literals and variants (skip empty strings)."""
    payload = planted if planted is not None else load_planted_pii()
    terms: list[str] = []
    seen: set[str] = set()
    for item in payload.get("literals") or []:
        if not isinstance(item, dict):
            continue
        for key in ("value", "variants"):
            raw = item.get(key)
            if isinstance(raw, str):
                if raw and raw not in seen:
                    seen.add(raw)
                    terms.append(raw)
            elif isinstance(raw, list):
                for v in raw:
                    if isinstance(v, str) and v and v not in seen:
                        seen.add(v)
                        terms.append(v)
    return terms


def _term_types(planted: dict[str, Any] | None = None) -> dict[str, str]:
    payload = planted if planted is not None else load_planted_pii()
    out: dict[str, str] = {}
    for item in payload.get("literals") or []:
        if not isinstance(item, dict):
            continue
        pii_type = str(item.get("type") or "UNKNOWN")
        value = str(item.get("value") or "")
        if value:
            out[value] = pii_type
        for v in item.get("variants") or []:
            if isinstance(v, str) and v:
                out[v] = pii_type
    return out


_NUMERIC_TERM = re.compile(r"[\d\s-]+")


@lru_cache(maxsize=256)
def _numeric_pattern(term: str) -> re.Pattern[str]:
    """
    A numeric literal (OTP, phone, account, Aadhaar) only counts when it stands alone.

    Checkpoints and logs hold thousands of timestamps with microseconds
    ("…:51.482913+00:00") and long floats, so a plain substring search for a 6-digit
    OTP matches by coincidence now and then. Real leaks look like "OTP 482913" or
    "call 9876543210", which this still catches.
    """
    return re.compile(rf"(?<![\d.]){re.escape(term)}(?!\d)")


def _term_in(term: str, text: str) -> bool:
    if _NUMERIC_TERM.fullmatch(term):
        return _numeric_pattern(term).search(text) is not None
    return term in text


def _search_text(text: str, terms: list[str], type_map: dict[str, str], location: str) -> list[Hit]:
    hits: list[Hit] = []
    for term in terms:
        if term and _term_in(term, text):
            hits.append(Hit(location=location, pii_type=type_map.get(term, "UNKNOWN")))
    return hits


def _decode_blob(blob: bytes) -> str:
    for decoder in (_try_json, _try_msgpack_jsonish):
        text = decoder(blob)
        if text:
            return text
    return blob.decode("utf-8", errors="ignore")


def _try_json(blob: bytes) -> str:
    try:
        parsed = json.loads(blob)
    except Exception:
        return ""
    return json.dumps(parsed, default=str)


def _try_msgpack_jsonish(blob: bytes) -> str:
    """Decode LangGraph checkpoint blobs (ormsgpack), falling back to msgpack (EC-LOG-04)."""
    for module in ("ormsgpack", "msgpack"):
        try:
            lib = __import__(module)
        except ImportError:
            continue
        try:
            parsed = lib.unpackb(blob) if module == "ormsgpack" else lib.unpackb(blob, raw=False)
        except Exception:
            continue
        return json.dumps(parsed, default=str, ensure_ascii=False)
    return ""


def sweep_db(db_url: str, terms: list[str], *, type_map: dict[str, str] | None = None) -> list[Hit]:
    """Scan every text/json column in the eval SQLite DB (+ checkpoint file if present)."""
    tmap = type_map or _term_types()
    if not db_url.startswith("sqlite:///"):
        return []
    path = Path(db_url.removeprefix("sqlite:///"))
    hits: list[Hit] = []
    hits.extend(_sqlite_file_scan(path, terms, tmap, label=f"db:{path.name}"))
    ckpt = path.parent / "checkpoints.sqlite3"
    if ckpt.is_file():
        hits.extend(_sqlite_file_scan(ckpt, terms, tmap, label=f"checkpoint:{ckpt.name}"))
    for base in (path, ckpt):
        for suffix in ("-wal", "-journal", "-shm"):
            side = Path(str(base) + suffix)
            if side.is_file():
                hits.extend(_raw_file_scan(side, terms, tmap, f"db-sidecar:{side.name}"))
    return hits


def db_locations(db_url: str) -> list[str]:
    """The DB-side locations ``sweep_db`` searches, for the report's location count."""
    if not db_url.startswith("sqlite:///"):
        return []
    path = Path(db_url.removeprefix("sqlite:///"))
    ckpt = path.parent / "checkpoints.sqlite3"
    found = [f"db:{path.name}"]
    if ckpt.is_file():
        found.append(f"checkpoint:{ckpt.name}")
    for base in (path, ckpt):
        found.extend(
            f"sidecar:{base.name}{suffix}"
            for suffix in ("-wal", "-journal", "-shm")
            if Path(str(base) + suffix).is_file()
        )
    return found


def _sqlite_file_scan(
    path: Path,
    terms: list[str],
    type_map: dict[str, str],
    *,
    label: str,
) -> list[Hit]:
    hits: list[Hit] = []
    conn = sqlite3.connect(str(path))
    try:
        tables = [
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]
        for table in tables:
            rows = conn.execute(f"SELECT * FROM [{table}]").fetchall()
            for row in rows:
                for cell in row:
                    if cell is None:
                        continue
                    if isinstance(cell, bytes):
                        text = _decode_blob(cell)
                        loc = f"{label}:{table}"
                    else:
                        text = str(cell)
                        loc = f"{label}:{table}"
                    hits.extend(_search_text(text, terms, type_map, loc))
    finally:
        conn.close()
    return hits


def _raw_file_scan(path: Path, terms: list[str], type_map: dict[str, str], label: str) -> list[Hit]:
    try:
        data = path.read_bytes()
    except OSError:
        return []
    text = data.decode("utf-8", errors="ignore")
    return _search_text(text, terms, type_map, label)


def sweep_path(
    path: Path,
    terms: list[str],
    *,
    type_map: dict[str, str] | None = None,
) -> list[Hit]:
    """File or directory walk; never scans ``evals/fixtures``."""
    tmap = type_map or _term_types()
    root = path.resolve()
    if _is_under_fixtures(root):
        return []
    hits: list[Hit] = []
    if root.is_file():
        return _scan_file(root, terms, tmap)
    if not root.is_dir():
        return []
    for child in root.rglob("*"):
        if not child.is_file():
            continue
        if _is_under_fixtures(child):
            continue
        hits.extend(_scan_file(child, terms, tmap))
    return hits


def _scan_file(path: Path, terms: list[str], type_map: dict[str, str]) -> list[Hit]:
    if _is_under_fixtures(path):
        return []
    label = str(path)
    try:
        if path.suffix in {".sqlite3", ".db"}:
            return _sqlite_file_scan(path, terms, type_map, label=f"sqlite:{path.name}")
        raw = path.read_bytes()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="ignore")
    return _search_text(text, terms, type_map, label)


def _is_under_fixtures(path: Path) -> bool:
    try:
        path.resolve().relative_to(FIXTURES_EXCLUDE)
        return True
    except ValueError:
        return False


def sweep_all(
    *,
    db_url: str,
    log_dir: Path,
    samples_dir: Path,
    report_paths: list[Path],
    terms: list[str] | None = None,
    exclude_dirs: list[Path] | None = None,
) -> SweepResult:
    """Run the full GC-10 sweep across DB, logs, samples, and report files."""
    search = terms if terms is not None else collect_search_terms()
    type_map = _term_types()
    hits: list[Hit] = []
    locations = 0

    db_hits = sweep_db(db_url, search, type_map=type_map)
    hits.extend(db_hits)
    locations += max(1, len(db_locations(db_url)))

    for directory, _name in (
        (log_dir, "logs"),
        (samples_dir, "samples"),
    ):
        if directory.is_dir():
            dir_hits = sweep_path(directory, search, type_map=type_map)
            hits.extend(dir_hits)
            locations += 1

    for report in report_paths:
        if report.is_file():
            hits.extend(sweep_path(report, search, type_map=type_map))
        locations += 1

    if exclude_dirs:
        for d in exclude_dirs:
            if d.resolve() == FIXTURES_EXCLUDE:
                continue
            if d.is_dir():
                hits.extend(sweep_path(d, search, type_map=type_map))
                locations += 1

    # Deduplicate (location, type) pairs while preserving order.
    seen: set[tuple[str, str]] = set()
    unique: list[Hit] = []
    for h in hits:
        key = (h.location, h.pii_type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)

    return SweepResult(hits=unique, locations_scanned=locations)
