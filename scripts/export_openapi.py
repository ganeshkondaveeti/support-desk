"""Dump the frozen OpenAPI schema to docs/openapi.json (P8.1)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import create_app  # noqa: E402


def main() -> int:
    application = create_app(
        load_facts=True,
        enable_scheduler=False,
        enable_bootstrap=False,
        enable_worker=False,
    )
    schema = application.openapi()
    out = ROOT / "docs" / "openapi.json"
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out} paths={len(schema.get('paths', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
