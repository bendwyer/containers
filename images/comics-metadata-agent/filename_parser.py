"""Parse CBZ filenames into a best-guess (series_name, issue_number).

The pipeline's convert-comics step already normalizes filenames to a
predictable `<series>, <issue>.cbz` shape for most cases, but:

- Some items have no issue number (graphic novels): `Inferno Girl Red, 1.cbz`
  where `1` is really "volume 1", not "issue 1".
- Trade-paperback labels leak: `C.O.W.L., Vol. 1 Principles of Power 1.cbz`.
- Casing varies — `RADIANT BLACK, 5.cbz` vs `Radiant Black, 1.cbz`.

This parser is best-effort: it gives the agent a starting point. The agent
can override if context makes the parse wrong.
"""

from __future__ import annotations

import re
from pathlib import Path


# Trailing integer = issue number. Greedy capture keeps "Vol. 1" in the
# series portion when an issue number follows, e.g.
#   "C.O.W.L., Vol. 1 Principles of Power 1" → ("C.O.W.L., Vol. 1 Principles of Power", "1")
_TRAILING_INT_RE = re.compile(r"^(.+?)\s+(\d+)$")


def parse_filename(filename: str) -> dict:
    """Return {'raw_title': str, 'issue_guess': str|None} from a CBZ filename."""
    stem = Path(filename).stem
    match = _TRAILING_INT_RE.match(stem)
    if match:
        title = _clean_title(match.group(1))
        issue = match.group(2)
        return {"raw_title": title, "issue_guess": issue}
    return {"raw_title": _clean_title(stem), "issue_guess": None}


def _clean_title(s: str) -> str:
    """Strip trailing commas/whitespace artifacts left by the convert step."""
    return s.rstrip(",").strip()
