comics-metadata-agent
=====================

End-to-end metadata pipeline for the books library: matches CBZ items to
ComicVine or MangaBaka, normalizes per-bundle metadata via a bundle-aware
planner, writes ComicInfo.xml + moves files to canonical locations in
`/books/library/<lane>/`. Replaces the prior comictagger-only step in the
Argo workflows for both humble and kobo manga lanes.

Lanes:

- **comics** — agent matches via ComicVine. Filename pattern
  `{series} ({year}) #{issue}.cbz`, folder pattern `{series} ({volume_year})/`.
- **manga** — agent prefers MangaBaka (regular series); falls back to
  ComicVine for omnibuses and Western-licensee editions (Dark Horse Manga,
  VIZ, Yen Press, etc.). Filename pattern `{series} #{issue} ({year}).cbz`,
  folder pattern `{series}/`.

Decisions land in `<decision-log-dir>/<source_id>.jsonl` for audit + replay.

Design records: `project_metadata_agent.md` and
`project_agent_inline_metadata.md` in the user's memory store. Pipeline
integration: `workflowtemplate-process-humble.yaml` (both lanes) and
`workflowtemplate-process-kobo.yaml` (manga lane).

Layout
------

- `agent.py` — CLI entry point. Per-item matcher driven by Claude.
- `agent_runner.py` — per-item tool-use loop. Holds CV + MB + Kavita clients.
- `apply.py` — bundle-aware applier. Reads decision log, runs planner,
  writes ComicInfo.xml via comictagger `--id`, moves files into the library.
- `replan.py` — companion to apply. Walks the library, brings already-shelved
  files in line with the current planner output. Indexes by `(source,
  issue_id)` derived from each file's `<Web>` URL (or `<Notes>` fallback)
  so ComicVine and MangaBaka tags are handled cleanly side by side.
- `bundle_planner.py` — three-phase compiler (hydrate → group → plan).
  Source-aware grouping: `(source, publisher, base_name)` so MangaBaka and
  ComicVine items don't accidentally merge.
- `prompt.py` — system prompt, tool schemas, user-message builder. Source
  selection rule (rule 7) details when MangaBaka vs ComicVine wins.
- `filename_parser.py` — CBZ filename → (series, issue) best guess.
- `series_grouper.py` — cluster CBZs by canonical series name.
- `kavita_client.py` — Kavita REST client (auth + search + metadata).
- `comicvine_client.py` — ComicVine v1 client (search + volume + issue).
- `mangabaka_client.py` — MangaBaka v1 client (search + series detail).
- `cover_extractor.py` — CBZ → Anthropic vision image blocks.
- `decision_log.py` — JSONL append with schema validation; carries the
  `source` field (`comicvine` or `mangabaka`).
- `patches/` — applied to installed `mangabaka_talker` site-packages at
  build time. See `patches/README.md`.

Tests
-----

    cd containers/images/comics-metadata-agent
    python -m unittest discover -s . -v

Runs in `python:3.12-alpine3.23`. Only `requests` + `pillow` + `anthropic`
are needed for the suite; comictagger and mangabaka_talker are runtime-only
(installed in the production image, not under test here).

Usage
-----

Two CLI entry points: `agent.py` matches and writes the decision log;
`apply.py` consumes the log and shelves files. They run sequentially in the
workflow steps; for one-off triage, run them in turn against an
`_unmatched/` directory.

`agent.py`:

    agent.py \
        --unmatched-dir /scratch/incoming/<lane> \
        --lane <comics|manga> \
        --source-context /config/source.json \
        --decision-log-dir /books/library/.agent-decisions/<lane> \
        --kavita-url http://kavita.books.svc.cluster.local:5000 \
        --kavita-api-key-file /secret/kavita/credential \
        --comicvine-api-key-file /secret/comicvine/credential \
        --anthropic-credential-file /secret/anthropic/credential \
        --model claude-sonnet-4-6

`apply.py`:

    apply.py \
        --source-id <source_id> \
        --lane <comics|manga> \
        --unmatched-dir /scratch/incoming/<lane> \
        --decision-log-dir /books/library/.agent-decisions/<lane> \
        --kavita-url http://kavita.books.svc.cluster.local:5000 \
        --kavita-api-key-file /secret/kavita/credential \
        --comicvine-api-key-file /secret/comicvine/credential

Source context JSON shape. `source_id` identifies the acquisition group —
humble bundle_key, kobo batch label, etc.:

    {
      "source_id": "S4NqZxAkmRkKZmEt",
      "source_title": "Humble Comics Bundle: Massive-Verse by Image Comics",
      "inferred_publisher": "Image Comics",
      "inferred_year_range": [2021, 2025]
    }

`inferred_publisher` and `inferred_year_range` are optional hints that
sharpen prompt rule evaluation (publisher informs MangaBaka-vs-ComicVine
selection in the manga lane; year range narrows ComicVine search filters).

Authentication
--------------

The Anthropic credential file may hold either a console API key
(`sk-ant-api-...`) or a subscription OAuth token (`sk-ant-oat-...`).
Auto-detected from the prefix. OAuth requires the `oauth-2025-04-20` beta
header and the constrained `"You are Claude Code..."` system message; the
agent injects both automatically when an OAuth token is detected.
