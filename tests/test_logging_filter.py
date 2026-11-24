"""PIIScrubFilter covers logger args and exception text (P1.5)."""

from __future__ import annotations

import logging

from app.guardrails.logging_filter import JsonLogFormatter, PIIScrubFilter, configure_logging


def test_filter_redacts_planted_pan_in_msg_and_args(caplog) -> None:  # type: ignore[no-untyped-def]
    logger = logging.getLogger("test.pii.scrub")
    logger.setLevel(logging.INFO)
    logger.addFilter(PIIScrubFilter())

    pan = "ABCDE1234F"
    with caplog.at_level(logging.INFO, logger="test.pii.scrub"):
        logger.info("user pan %s", pan)

    assert pan not in caplog.text
    assert "[PAN]" in caplog.text


def test_filter_redacts_exception_text() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="boom",
        args=(),
        exc_info=None,
    )
    record.exc_text = "ValueError: leaked PAN ABCDE1234F"
    assert PIIScrubFilter().filter(record) is True
    assert record.exc_text is not None
    assert "ABCDE1234F" not in record.exc_text
    assert "[PAN]" in record.exc_text


def test_json_formatter_emits_object() -> None:
    configure_logging()
    record = logging.LogRecord(
        name="app",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="ok",
        args=(),
        exc_info=None,
    )
    line = JsonLogFormatter().format(record)
    assert line.startswith("{")
    assert '"msg": "ok"' in line


def test_allowlists_register_and_public_url() -> None:
    from app.guardrails import allowlists

    allowlists.register_corpus(
        scheme_ids=["large_cap", "gold_fof"],
        sections=["Exit load"],
        citation_urls=["https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"],
    )
    allowlists.register_themes(["fees_brokerage", "other"])
    assert "fees_brokerage" in allowlists.THEME_IDS
    assert "large_cap" in allowlists.SCHEME_IDS
    assert allowlists.is_public_url(
        "https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth"
    )
    assert allowlists.is_public_url("https://www.amfiindia.com/investor-corner")
    assert allowlists.is_public_url("https://sub.groww.in/x")
    assert not allowlists.is_public_url("https://evil.example/phish")
    assert not allowlists.is_public_url("not-a-url")


def test_filter_scrubs_exception_args() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=(ValueError, ValueError("PAN ABCDE1234F"), None),
    )
    assert PIIScrubFilter().filter(record) is True
    assert "ABCDE1234F" not in str(record.exc_info[1])


def test_json_formatter_with_exc_and_extra() -> None:
    record = logging.LogRecord(
        name="app",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="fail",
        args=(),
        exc_info=(RuntimeError, RuntimeError("x"), None),
    )
    record.run_id = "r_1"  # type: ignore[attr-defined]
    line = JsonLogFormatter().format(record)
    assert '"run_id": "r_1"' in line
    assert '"exc"' in line


def test_scrub_dict_args() -> None:
    from app.guardrails.logging_filter import _scrub_args

    scrubbed = _scrub_args({"pan": "ABCDE1234F", "n": 1})
    assert scrubbed == {"pan": "[PAN]", "n": 1}
