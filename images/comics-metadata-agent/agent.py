#!/usr/bin/env python3
"""Comics metadata agent — propose ComicVine matches for _unmatched/ items.

v0 is propose-only: decisions land in the JSONL log, but files stay in
`_unmatched/`. A future apply mode will replay the log to commit matches
via comictagger.

Usage:
    agent.py \\
        --unmatched-dir /books/incoming/comics/_unmatched \\
        --source-context /config/source.json \\
        --decision-log-dir /books/library/.agent-decisions \\
        --kavita-url http://kavita.books.svc.cluster.local \\
        --kavita-api-key-file /secret/kavita/api-key \\
        --comicvine-api-key-file /secret/comicvine/api-key \\
        --anthropic-api-key-file /secret/anthropic/api-key \\
        --model claude-sonnet-4-6

Source context file (JSON). `source_id` is a unique identifier for the
acquisition group — humble bundle_key, kobo batch label, etc.:

    {
      "source_id": "S4NqZxAkmRkKZmEt",
      "source_title": "Humble Comics Bundle: Massive-Verse by Image Comics",
      "inferred_publisher": "Image Comics",
      "inferred_year_range": [2021, 2025]
    }

Exit codes:
  0 = ran to completion (regardless of per-item match/uncertain outcomes)
  2 = configuration / input error (missing files, bad source context)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import anthropic
import requests

from agent_runner import AgentRunner
from comicvine_client import ComicVineClient
from decision_log import DecisionLog
from kavita_client import KavitaClient
from series_grouper import group_by_series


_VERSION = "0.1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        kavita_key = _read_secret(args.kavita_api_key_file)
        comicvine_key = _read_secret(args.comicvine_api_key_file)
        anthropic_key = _read_secret(args.anthropic_api_key_file)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        source_ctx = json.loads(args.source_context.read_text())
    except FileNotFoundError:
        print(f"ERROR: source context file not found: {args.source_context}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"ERROR: source context is not valid JSON: {e}", file=sys.stderr)
        return 2

    source_id = source_ctx.get("source_id")
    if not source_id:
        print("ERROR: source context missing source_id", file=sys.stderr)
        return 2

    if not args.unmatched_dir.is_dir():
        print(f"ERROR: unmatched-dir does not exist: {args.unmatched_dir}", file=sys.stderr)
        return 2

    items = sorted(args.unmatched_dir.glob("*.cbz"))
    if not items:
        print("No .cbz items in unmatched dir — nothing to do.")
        return 0

    session = requests.Session()
    kavita = KavitaClient(
        base_url=args.kavita_url,
        api_key=kavita_key,
        session=session,
    )
    comicvine = ComicVineClient(
        api_key=comicvine_key,
        user_agent=f"comics-metadata-agent/{_VERSION}",
        session=session,
    )
    claude = anthropic.Anthropic(api_key=anthropic_key)
    decision_log = DecisionLog(args.decision_log_dir, source_id=source_id)

    runner = AgentRunner(
        claude_client=claude,
        kavita=kavita,
        comicvine=comicvine,
        decision_log=decision_log,
        model=args.model,
        max_tool_rounds=args.max_tool_rounds,
    )

    groups = group_by_series(items)
    print(f"Processing {len(items)} items in {len(groups)} series groups")
    print(f"Source: {source_ctx.get('source_title', source_id)}")
    print(f"Decisions → {decision_log.path}")

    total_matches = 0
    total_uncertain = 0
    for key in sorted(groups.keys()):
        group = groups[key]
        print(f"\n=== Group {key!r} ({len(group)} items) ===")
        siblings = runner.run_group(group, source_ctx)
        matches = len(siblings)
        uncertain = len(group) - matches
        total_matches += matches
        total_uncertain += uncertain
        print(f"  matches: {matches}, uncertain: {uncertain}")

    print(f"\nTotals: {total_matches} match, {total_uncertain} uncertain")
    print(f"ComicVine API calls: {comicvine.call_count}")
    return 0


def _read_secret(path: Path) -> str:
    return path.read_text().strip()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Propose ComicVine matches for dead-lettered CBZ files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--unmatched-dir", required=True, type=Path,
                   help="Directory containing .cbz files awaiting triage.")
    p.add_argument("--source-context", required=True, type=Path,
                   help="Path to a JSON file with source_id + source_title.")
    p.add_argument("--decision-log-dir", required=True, type=Path,
                   help="Directory where per-source JSONL decision logs are appended.")
    p.add_argument("--kavita-url",
                   default="http://kavita.books.svc.cluster.local",
                   help="Base URL for the Kavita instance.")
    p.add_argument("--kavita-api-key-file", required=True, type=Path)
    p.add_argument("--comicvine-api-key-file", required=True, type=Path)
    p.add_argument("--anthropic-api-key-file", required=True, type=Path)
    p.add_argument("--model", default="claude-sonnet-4-6",
                   help="Claude model id.")
    p.add_argument("--max-tool-rounds", type=int, default=8,
                   help="Ceiling on tool-use iterations per item.")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
