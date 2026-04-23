comics-metadata-agent
=====================

Offline triage for the books pipeline's `_unmatched/` dead-letter queue in
the comics + manga lanes. Proposes ComicVine matches by combining: Kavita
library state (for coherence with existing groupings), ComicVine search
(for candidate volumes + issues), cover-image analysis via Claude Sonnet
vision, and sibling context accumulated within a series group.

Works for any source that produces CBZs through the pipeline — Humble
bundles, Kobo omnibus splits, etc. Gutenberg prose ebooks are out of scope
(different matching provider).

v0 is **propose-only**: every decision lands in a JSONL audit log on the
library PVC. Files stay in `_unmatched/`. A future apply-mode pass replays
the log to move confident matches into the library via comictagger.

Design record lives in `project_metadata_agent.md` in the user's memory
store; see also `reference_kavita_api.md` and the port memory for pipeline
integration points.

Layout
------

- `agent.py` — CLI entry point.
- `agent_runner.py` — per-item tool-use loop.
- `prompt.py` — system prompt + tool schemas + user-message builder.
- `filename_parser.py` — CBZ filename → (series, issue) best guess.
- `series_grouper.py` — cluster CBZs by canonical series name.
- `kavita_client.py` — Kavita REST client (auth + search + metadata).
- `comicvine_client.py` — ComicVine v1 client (search + volume + issue).
- `cover_extractor.py` — CBZ → Anthropic vision image blocks.
- `decision_log.py` — JSONL append with schema validation.

Tests
-----

    cd containers/images/comics-metadata-agent
    python -m unittest discover -s . -v

(Runs in the python:3.12-alpine3.23 image; only `requests` is needed for
the test suite. `anthropic` is a runtime-only dependency.)

Usage
-----

    agent.py \
        --unmatched-dir /books/incoming/comics/_unmatched \
        --source-context /config/source.json \
        --decision-log-dir /books/library/.agent-decisions \
        --kavita-url http://kavita.books.svc.cluster.local \
        --kavita-api-key-file /secret/kavita/api-key \
        --comicvine-api-key-file /secret/comicvine/api-key \
        --anthropic-api-key-file /secret/anthropic/api-key \
        --model claude-sonnet-4-6

Source context JSON shape. `source_id` is the identifier for the
acquisition group — humble bundle_key, kobo batch label, etc:

    {
      "source_id": "S4NqZxAkmRkKZmEt",
      "source_title": "Humble Comics Bundle: Massive-Verse by Image Comics",
      "inferred_publisher": "Image Comics",
      "inferred_year_range": [2021, 2025]
    }
