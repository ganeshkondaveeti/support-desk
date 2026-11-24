"""A-vs-B drift report for overlapping facts (architecture §5.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.corpus.loader import Corpus, load_corpus


@dataclass(frozen=True, slots=True)
class DriftRow:
    scheme_id: str
    expense_ratio_a: str | None
    expense_ratio_b: str | None
    fund_size_a: str | None
    fund_size_b: str | None
    riskometer_a: str | None
    riskometer_b: str | None
    exit_load: str  # "unchanged" or a description


# Expected drift table from architecture §5.1 (for regression tests).
EXPECTED_DRIFT: dict[str, dict[str, str]] = {
    "large_cap": {
        "expense": "1.04% → 1.02%",
        "fund_size": "₹37,808.31 Cr → ₹40,197.89 Cr",
        "riskometer": "Moderately High → Very High",
        "exit_load": "unchanged",
    },
    "mid_cap": {
        "expense": "0.75% → 0.74%",
        "fund_size": "₹97,350.48 Cr → ₹1,05,142.69 Cr",
        "riskometer": "Moderately High → Very High",
        "exit_load": "unchanged",
    },
    "small_cap": {
        "expense": "0.77% → 0.75%",
        "fund_size": "₹38,809.48 Cr → ₹41,679.00 Cr",
        "riskometer": "Moderately High → Very High",
        "exit_load": "unchanged",
    },
    "gold_fof": {
        "expense": "0.2% → 0.20%",
        "fund_size": "₹12,121.18 Cr → ₹11,197.05 Cr",
        "riskometer": "(not in B)",
        "exit_load": "unchanged",
    },
}


def compute_drift(corpus: Corpus | None = None) -> list[DriftRow]:
    corpus = corpus or load_corpus()
    rows: list[DriftRow] = []
    schemes = sorted(
        {
            sid
            for (sid, _sec, snap) in corpus.all_blocks
            if snap == "A"
            and any(
                k == (sid, "Key fund facts", "B") or k == (sid, "Exit load", "B")
                for k in corpus.all_blocks
            )
        }
    )
    # Prefer the four schemes in the arch table order.
    order = ["large_cap", "mid_cap", "small_cap", "gold_fof"]
    for scheme_id in order:
        if scheme_id not in {sid for (sid, _, _) in corpus.all_blocks}:
            continue
        a_facts = corpus.all_blocks.get((scheme_id, "Key fund facts", "A"))
        b_facts = corpus.all_blocks.get((scheme_id, "Key fund facts", "B"))
        a_exit = corpus.all_blocks.get((scheme_id, "Exit load", "A"))
        b_exit = corpus.all_blocks.get((scheme_id, "Exit load", "B"))

        exp_a = _field(a_facts.text if a_facts else "", "Expense ratio")
        exp_b = _field(b_facts.text if b_facts else "", "Expense ratio")
        size_a = _field(a_facts.text if a_facts else "", "Fund size")
        size_b = _field(b_facts.text if b_facts else "", "Fund size")
        risk_a = _riskometer(a_facts.text if a_facts else "")
        risk_b = _riskometer(b_facts.text if b_facts else "")

        exit_status = "unchanged"
        if a_exit and b_exit:
            if _norm_exit(a_exit.text) != _norm_exit(b_exit.text):
                exit_status = f"{a_exit.text} → {b_exit.text}"
        elif a_exit and not b_exit:
            exit_status = "B missing"
        elif b_exit and not a_exit:
            exit_status = "A missing"

        rows.append(
            DriftRow(
                scheme_id=scheme_id,
                expense_ratio_a=exp_a,
                expense_ratio_b=exp_b,
                fund_size_a=size_a,
                fund_size_b=size_b,
                riskometer_a=risk_a,
                riskometer_b=risk_b if b_facts else None,
                exit_load=exit_status,
            )
        )
        _ = schemes
    return rows


def format_drift_table(rows: list[DriftRow] | None = None) -> str:
    rows = rows if rows is not None else compute_drift()
    header = "| Scheme | Expense ratio A → B | Fund size A → B | Riskometer A → B | Exit load |"
    sep = "| --- | --- | --- | --- | --- |"
    lines = [header, sep]
    for row in rows:
        exp = _arrow(row.expense_ratio_a, row.expense_ratio_b)
        size = _arrow(row.fund_size_a, row.fund_size_b)
        if row.riskometer_b is None and row.scheme_id == "gold_fof":
            risk = f"{row.riskometer_a or '?'} → (not in B)" if row.riskometer_a else "(not in B)"
        else:
            risk = _arrow(row.riskometer_a, row.riskometer_b)
        lines.append(f"| `{row.scheme_id}` | {exp} | {size} | {risk} | {row.exit_load} |")
    return "\n".join(lines)


def main() -> None:
    print(format_drift_table())


def _field(text: str, label: str) -> str | None:
    if not text:
        return None
    labels = [label]
    if label.lower().startswith("fund size"):
        labels = ["Fund size (AUM)", "Fund size"]
    for candidate in labels:
        # Capture through % or Cr so decimals in 1.02% / ₹37,808.31 Cr survive.
        match = re.search(
            rf"{re.escape(candidate)}\s*:\s*(.+?)(?:\.\s+[A-Z]|\.\s*$|\.$|$)",
            text,
            re.I,
        )
        if match:
            return match.group(1).strip().rstrip(".")
    return None


def _riskometer(text: str) -> str | None:
    if not text:
        return None
    # A: "Riskometer: Moderately High Riskometer."
    m = re.search(r"Riskometer:\s*([^.]+)", text, re.I)
    if m:
        val = m.group(1).replace("Riskometer", "").strip()
        return val
    # B: "… is rated Very High risk."
    m2 = re.search(r"rated\s+([A-Za-z ]+?)\s+risk", text, re.I)
    if m2:
        return m2.group(1).strip()
    return None


def _arrow(a: str | None, b: str | None) -> str:
    if a is None and b is None:
        return "—"
    if a is None:
        return f"— → {b}"
    if b is None:
        return f"{a} → (not in B)"
    return f"{a} → {b}"


def _norm_exit(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().rstrip(".").lower()


if __name__ == "__main__":
    main()
