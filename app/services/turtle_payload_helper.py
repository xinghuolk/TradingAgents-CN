"""
Shared helper for extracting canonical value_turtle_payload from analysis result data.
Priority order (spec §4.2):
  1. result_data["value_turtle_payload"]
  2. result_data["state"]["value_turtle_payload"]
  3. result_data["reports"]["value_turtle_payload"]
  4. reports_dir / "value_turtle_payload.json"  (disk fallback)
Returns "" when no valid (non-blank) payload found; never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("app.turtle_payload_helper")


def extract_turtle_payload(
    result_data: Optional[dict[str, Any]],
    reports_dir: Optional[Path] = None,
) -> str:
    """Return the canonical value_turtle_payload string, or '' if absent."""
    if not result_data:
        return ""

    # Priority 1: top-level field
    candidate = result_data.get("value_turtle_payload", "")
    if isinstance(candidate, str) and candidate.strip():
        return candidate

    # Priority 2: state sub-dict
    state = result_data.get("state") or {}
    if isinstance(state, dict):
        candidate = state.get("value_turtle_payload", "")
        if isinstance(candidate, str) and candidate.strip():
            return candidate

    # Priority 3: reports sub-dict
    reports = result_data.get("reports") or {}
    if isinstance(reports, dict):
        candidate = reports.get("value_turtle_payload", "")
        if isinstance(candidate, str) and candidate.strip():
            return candidate

    # Priority 4: disk fallback
    if reports_dir is not None:
        disk_path = Path(reports_dir) / "value_turtle_payload.json"
        try:
            if disk_path.exists():
                content = disk_path.read_text(encoding="utf-8").strip()
                if content:
                    logger.info(f"📂 Loaded value_turtle_payload from disk: {disk_path}")
                    return content
        except Exception as exc:
            logger.warning(f"⚠️ Failed to read disk payload at {disk_path}: {exc}")

    return ""
