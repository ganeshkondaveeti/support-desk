"""Corpus loader, golden resolved blocks, drift, quarantine, sentences (P2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.corpus.drift import EXPECTED_DRIFT, compute_drift, format_drift_table
from app.corpus.loader import reload_corpus
from app.corpus.normalize import CANONICAL_SECTION_LIST, join_label_value_pairs, normalize_b_scheme
from app.corpus.quarantine import assert_no_quarantine, strip_quarantined
from app.corpus.sentences import select_sentences
from app.guardrails import allowlists

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "fixtures" / "corpus_resolved_golden.json"


@pytest.fixture(scope="module")
def corpus():
    return reload_corpus(ROOT / "data")


def test_six_schemes_no_quarantine(corpus) -> None:  # type: ignore[no-untyped-def]
    assert corpus.scheme_ids == {
        "large_cap",
        "mid_cap",
        "small_cap",
        "gold_fof",
        "silver_fof",
        "elss_tax_saver",
    }
    for block in corpus.blocks.values():
        assert_no_quarantine(block.text)
        assert "10 Dec 1999" not in block.text
        assert "9,67,910" not in block.text


def test_allowlists_registered(corpus) -> None:  # type: ignore[no-untyped-def]
    assert len(allowlists.SCHEME_IDS) == 6
    assert len(allowlists.SECTIONS) == 7
    assert len(allowlists.CITATION_URLS) == 6
    assert all("groww.in" in url for url in allowlists.CITATION_URLS)


def test_plan_examples(corpus) -> None:  # type: ignore[no-untyped-def]
    large_facts = corpus.get("large_cap", "Key fund facts")
    assert large_facts is not None
    assert large_facts.snapshot == "B"
    assert large_facts.last_checked == "01-Sep-2026"
    assert "Expense ratio: 1.02%." in large_facts.text

    silver_bench = corpus.get("silver_fof", "Benchmark")
    assert silver_bench is not None
    assert silver_bench.snapshot == "A"
    assert silver_bench.last_checked == "28-Jun-2026"

    assert corpus.get("elss_tax_saver", "Benchmark") is None
    elss_exit = corpus.get("elss_tax_saver", "Exit load")
    assert elss_exit is not None
    assert elss_exit.snapshot == "B"
    assert "Nil" in elss_exit.text


def test_golden_resolved_blocks(corpus) -> None:  # type: ignore[no-untyped-def]
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(expected) == 6 * 7
    for scheme in sorted(corpus.scheme_ids):
        for section in CANONICAL_SECTION_LIST:
            key = f"{scheme}|{section}"
            block = corpus.get(scheme, section)
            want = expected[key]
            if want is None:
                assert block is None, key
            else:
                assert block is not None, key
                assert block.snapshot == want["snapshot"], key
                assert block.last_checked == want["last_checked"], key
                assert block.text == want["text"], key
                assert block.source_url == want["source_url"], key


def test_b_normalization_label_join() -> None:
    raw = "Expense ratio\n1.02%\nRating\n4\nFund size (AUM)\n₹40,197.89 Cr"
    joined = join_label_value_pairs(raw)
    assert "Expense ratio: 1.02%." in joined
    assert "Fund size (AUM): ₹40,197.89 Cr." in joined
    assert "Rating" not in joined


def test_normalize_b_quarantines_launch_and_aum() -> None:
    payload = json.loads(
        (ROOT / "data/processed/hdfc_large_cap_fund.json").read_text(encoding="utf-8")
    )
    normalized = normalize_b_scheme(payload)
    blob = " ".join(normalized.values())
    assert "10 Dec 1999" not in blob
    assert "9,67,910" not in blob
    assert "Rating" not in blob


def test_strip_quarantined_helper() -> None:
    text = (
        "Hello. This scheme was made available to investors on 10 Dec 1999. "
        "The fund currently has an Asset Under Management(AUM) of ₹9,67,910 Cr and more. "
        "Keep me."
    )
    clean, removed = strip_quarantined(text)
    assert "10 Dec 1999" not in clean
    assert "9,67,910" not in clean
    assert "Keep me." in clean
    assert removed


def test_drift_matches_architecture(corpus) -> None:  # type: ignore[no-untyped-def]
    rows = {row.scheme_id: row for row in compute_drift(corpus)}
    for scheme_id, expected in EXPECTED_DRIFT.items():
        row = rows[scheme_id]
        assert f"{row.expense_ratio_a} → {row.expense_ratio_b}" == expected["expense"]
        assert f"{row.fund_size_a} → {row.fund_size_b}" == expected["fund_size"]
        if expected["riskometer"] == "(not in B)":
            assert row.riskometer_b is None
        else:
            assert f"{row.riskometer_a} → {row.riskometer_b}" == expected["riskometer"]
        assert row.exit_load == expected["exit_load"]
    table = format_drift_table(list(rows.values()))
    assert "`large_cap`" in table
    assert "1.04% → 1.02%" in table


def test_select_expense_ratio_sentence(corpus) -> None:  # type: ignore[no-untyped-def]
    block = corpus.get("large_cap", "Key fund facts")
    assert block is not None
    picked = select_sentences(block, "What is the expense ratio?")
    assert picked
    assert "Expense ratio: 1.02%." in picked[0]


def test_app_boots_with_corpus() -> None:
    from app.main import create_app

    app = create_app(load_facts=True)
    assert app.title == "The Support Desk"
