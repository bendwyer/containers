"""Persist agent decisions as per-source JSONL on the library PVC.

The log is the audit trail: "three weeks from now, why did the agent
pick volume X for this item?" Every decision captures not just the
final answer but also the inputs the agent saw and the candidates it
rejected. Without this, iteration on the prompt is guessing.

File path: <log_dir>/<source_id>.jsonl
Format:    one JSON object per line, append-only. Lines are independent —
           truncation or partial writes don't corrupt earlier entries.

`source_id` is the identifier for the acquisition that produced these
items: humble `bundle_key` for Humble bundles, a user-supplied label for
Kobo batches or Gutenberg collections.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {"filename", "decision", "reasoning"}
VALID_DECISIONS = {"match", "uncertain"}
VALID_CONFIDENCE = {"high", "medium"}


class DecisionSchemaError(ValueError):
    """Decision dict failed schema validation."""


class DecisionLog:
    def __init__(self, log_dir: Path | str, source_id: str):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.log_dir / f"{source_id}.jsonl"
        self._source_id = source_id

    def append(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Validate + stamp + write a decision. Returns the final record
        (with added fields) for callers that want it."""
        record = _validate_and_normalize(decision, self._source_id)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def read_all(self) -> list[dict[str, Any]]:
        """Read all decisions from this log. Used in tests + the future
        apply-mode to replay proposals."""
        if not self.path.exists():
            return []
        out = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def _validate_and_normalize(decision: dict[str, Any], source_id: str) -> dict[str, Any]:
    missing = REQUIRED_FIELDS - decision.keys()
    if missing:
        raise DecisionSchemaError(f"missing required fields: {sorted(missing)}")
    if decision["decision"] not in VALID_DECISIONS:
        raise DecisionSchemaError(
            f"decision must be one of {VALID_DECISIONS}, got {decision['decision']!r}"
        )
    if decision["decision"] == "match":
        if not decision.get("issue_id"):
            raise DecisionSchemaError("match decisions require issue_id")
        confidence = decision.get("confidence")
        if confidence and confidence not in VALID_CONFIDENCE:
            raise DecisionSchemaError(
                f"confidence must be one of {VALID_CONFIDENCE}, got {confidence!r}"
            )
    # Copy + stamp. Never mutate the caller's dict.
    record = dict(decision)
    record["source_id"] = source_id
    record["when"] = datetime.now(timezone.utc).isoformat()
    return record
