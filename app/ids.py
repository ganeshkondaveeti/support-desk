"""Id helpers that avoid PII false positives in hex strings."""

from __future__ import annotations

import uuid

# Groups of 3 + '-' break PHONE (10 digits), PAN (5 letters + 4 digits), and
# Aadhaar (4-4-4 digit groups). Do not use letter separators like 'x' — they
# count toward [A-Z] under IGNORECASE and feed the PAN compact window.
_SEP = "-"
_WIDTH = 3


def safe_hex_id(prefix: str, *, nibble_groups: int = _WIDTH) -> str:
    """Build ``{prefix}_{hex groups joined by '-'}`` from a uuid hex."""
    raw = uuid.uuid4().hex
    parts = [raw[i : i + nibble_groups] for i in range(0, len(raw), nibble_groups)]
    return f"{prefix}_{_SEP.join(parts)}"


def safe_hex_display(value: str, *, width: int = _WIDTH) -> str:
    """Display a hex digest with separators so pii.scan cannot false-positive."""
    return _SEP.join(value[i : i + width] for i in range(0, len(value), width))
