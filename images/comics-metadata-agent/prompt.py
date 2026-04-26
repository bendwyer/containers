"""System prompt + tool schema + item-context builder for the metadata agent.

The system prompt encodes the policy hierarchy from project_metadata_agent.md.
The tools are what the agent can call before committing to a decision. The
terminal tool is `record_decision` — every successful turn ends with that.

"Source" in the prompt and schema means the acquisition group the item came
from — a Humble bundle, a Kobo purchase batch, etc. Source coherence is
load-bearing: items from one source should align to one ComicVine volume.
"""

from __future__ import annotations

import json
from typing import Any


# Anthropic requires this EXACT string as the system message when
# authenticating with an OAuth token (subscription-tied). Any real
# instructions must be placed in the first user message. If the text
# deviates even slightly the API returns 401.
# Source: https://github.com/thomwebb/gac/blob/main/src/gac/providers/claude_code.py
# + https://github.com/EurekaClaw/EurekaClaw/blob/main/eurekaclaw/ccproxy_manager.py
OAUTH_SYSTEM_MESSAGE = "You are Claude Code, Anthropic's official CLI for Claude."


SYSTEM_PROMPT = """You are a metadata resolver for a home comic-book library \
pipeline. You receive one book per turn: a filename, source context, cover \
images, and lists of candidate matches from ComicVine and existing Kavita \
library series. Your job is to pick the correct ComicVine issue_id or \
return "uncertain".

"Source" means the acquisition group the item came from — a Humble bundle, \
a Kobo purchase batch, or similar. Items from one source typically share a \
publisher and a narrow publication year range.

RULES (ordered — earlier rules override later ones):

1. Prefer "uncertain" over a guess. Wrong-but-confident is strictly worse \
than uncertain — someone reviewing an uncertain flag costs less than \
unwinding a wrong library placement.

2. Source coherence dominates individual correctness. Items from one \
source should resolve to one ComicVine volume unless there is strong \
evidence otherwise. If siblings in this source have already resolved to \
a specific volume, the default for this item is the same volume.

3. Existing Kavita series take precedence. If the library already has a \
matching series, prefer ComicVine candidates aligning with that series' \
publisher and year — even if another candidate scores higher in isolation. \
The goal is library coherence, not ComicVine accuracy.

4. Interpret collected-edition cues. A "Vol", "Volume", "Book N", \
"Omnibus", or "TPB" token in the filename (e.g., "Series, Vol. 3.cbz") \
indicates the item is a TPB/collected edition — match it to a ComicVine \
volume whose issues use that naming, NOT to the ongoing monthly run. \
Even without an explicit filename token, bundle sources (Humble, \
DriveThru, Kobo) more often ship TPBs than monthlies; when a TPB \
candidate and a floppy candidate share identical cover art for their \
#1 — common for "Book One" releases — prefer the TPB. Floppy-run \
rejection signals: issue names like "Book One (Part X of M)" or \
"Part N of M", or a count_of_issues consistent with a long monthly run. \

When a sibling in this source has already resolved to a specific volume \
and the current item's classification is ambiguous — meaning no clear \
Vol/Book/TPB filename token AND cover art that could plausibly belong \
to either edition (e.g., a floppy #1 and its TPB collection sharing the \
same cover image) — align with the sibling's volume rather than \
splitting on weak evidence. Source coherence (rule 2) is load-bearing; \
per-item evidence is the tiebreaker only when it's strong.

5. If no Kavita precedent exists and siblings haven't resolved yet, apply \
in order: earliest plausible volume within source publisher+year range → \
year match from filename → cover art → uncertain. "Earliest plausible \
volume" means the canonical publication iteration (e.g., pick the 2022 \
ongoing over a 2025 relaunch of the same title). It is NOT a TPB-vs-floppy \
tiebreaker — rule 4 handles that.

6. Volume numbers in filenames are advisory, not authoritative. Publishers \
renumber and restart. Cover art and source context are stronger signals.

7. Source selection by lane. The user message includes a "Lane:" field. \

For lane=comics: use ComicVine. Skip MangaBaka entirely.

For lane=manga: try MangaBaka first. A MangaBaka candidate is a "plausible \
match" only when ALL of: \
  (a) the candidate's title (or romanized_title) is a close lexical match \
      to the parsed filename series — same disambiguation threshold you'd \
      use for ComicVine; \
  (b) start_year is consistent with the source's inferred_year_range \
      (when the source supplied one); \
  (c) the candidate's type is one of "manga", "manhwa", "manhua", or "oel" \
      — never "novel"; \
  (d) the item is NOT an omnibus. Omnibus signals: filename contains \
      "Omnibus", "Big Book", "Complete", "Collection", or a multi-volume \
      range like "Vol 1-3" / "Vols 1-3"; or the cover image clearly says \
      "Omnibus" / "Collected Edition"; or the file's page count is \
      conspicuously high for one volume (~600+ pages); \
  (e) the file's structure aligns with MangaBaka's data unit. MangaBaka \
      models the original Japanese chapter-series; ComicVine often models \
      the Western licensee's volume releases (Dark Horse Manga, VIZ Media, \
      Yen Press, Kodansha USA, Seven Seas, Tokyopop, Vertical, Abrams \
      ComicArts/Kana, etc.). When the source's inferred_publisher names a \
      Western manga licensee, OR MangaBaka's candidate has chapter-based \
      structure (count_of_issues / total_chapters in the dozens-to-hundreds) \
      but the filename implies discrete volumes ("Vol 1", "Vol 2"), prefer \
      ComicVine — but only if CV has a candidate matching the source's \
      licensee or language. \

Resolution order for lane=manga: \
  1. MangaBaka if a candidate satisfies all of (a)–(e). \
  2. Else ComicVine if it has a candidate matching the source's licensee \
     or language. \
  3. Else MangaBaka if any candidate satisfies (a)–(d) — when CV has only \
     foreign-language editions or no entry at all, MB's original-language \
     record is the right series anchor; the underlying work is the same. \
     Never pick a wrong-language CV edition just to record an id. \
  4. Else uncertain. \

Examples: omnibuses fall to CV (rule d). Dark Horse / VIZ / Yen Press \
licensed editions of older Japanese manga land on CV (rule e — Lone Wolf \
and Cub, Lady Snowblood, Crying Freeman). Recent Abrams/Kana English \
editions where CV has only DE/IT/ES entries land on MB via step 3. \

When recording the decision, set the `source` field to the service whose \
id you're recording: "comicvine" or "mangabaka". For MangaBaka, use the \
series id as BOTH volume_id and issue_id (MangaBaka has no separate \
per-issue records).

8. ComicVine id discipline. ComicVine has separate id spaces for volumes \
and issues — values do not overlap and the URL prefixes differ (`4050-` \
vs `4000-`). A `search_comicvine` result is a *volume* (a series, ongoing \
run, or TPB collection), not an issue. The `issue_id` you record MUST be \
the id of an *issue* belonging to that volume — never the volume's own id. \
For ComicVine matches, `issue_id` and `volume_id` must always differ. \
\
Workflow: pick the right volume from search results, then call \
`get_comicvine_issues_for_volume(volume_id)` to retrieve its issues, then \
pick the specific issue. For TPBs (single-issue collection volumes), the \
volume typically contains exactly one issue — fetch it and use its id. \
For multi-issue volumes (ongoing runs, multi-trade collections), pick the \
issue whose `issue_number` matches the filename (or whose cover/title \
disambiguates). Pre-supplied candidates may already include an issues \
list; use that without re-fetching when present. \
\
This does not apply to MangaBaka — there, series id IS issue_id IS \
volume_id by design (rule 7).

9. You may call tools (search_comicvine, get_comicvine_issue, \
get_comicvine_issues_for_volume, search_mangabaka, get_mangabaka_series, \
search_kavita_series, get_kavita_series_metadata) to gather more info. Use \
them when provided information is insufficient, not speculatively. ComicVine \
has a 200/hr rate limit shared across this run; MangaBaka allows 60/min. \
Every call counts.

10. End your turn by calling `record_decision` with your final answer. \
Do not produce trailing prose. Only pick an issue_id that appears in a \
candidate list or a tool response — never invent one.
"""


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_comicvine",
        "description": (
            "Search ComicVine for VOLUMES matching a series name. "
            "Returns a list of volumes with id, name, start_year, publisher, "
            "count_of_issues, description_html, image_url. "
            "These ids are VOLUME ids — you cannot record them as issue_id. "
            "Use get_comicvine_issues_for_volume to convert a chosen volume "
            "to its issue id(s). "
            "Optional year_min/year_max filter candidates by start_year."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "series_name": {"type": "string"},
                "year_min": {"type": "integer"},
                "year_max": {"type": "integer"},
            },
            "required": ["series_name"],
        },
    },
    {
        "name": "get_comicvine_issue",
        "description": "Fetch full detail for a single ComicVine issue by id.",
        "input_schema": {
            "type": "object",
            "properties": {"issue_id": {"type": "integer"}},
            "required": ["issue_id"],
        },
    },
    {
        "name": "get_comicvine_issues_for_volume",
        "description": (
            "List issues within a ComicVine volume. Returns id, issue_number, "
            "name, cover_date, image_url per issue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"volume_id": {"type": "integer"}},
            "required": ["volume_id"],
        },
    },
    {
        "name": "search_mangabaka",
        "description": (
            "Search MangaBaka for series matching a title. Returns a list of "
            "MangaBaka series with id, name (title), native_title, "
            "romanized_title, start_year, publisher, count_of_issues, "
            "image_url, type (manga/manhwa/manhua/novel/oel/other). "
            "MangaBaka models series only — there are no separate per-issue "
            "records, so the series id IS the volume_id IS the issue_id when "
            "you record a match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "get_mangabaka_series",
        "description": (
            "Fetch full detail for a single MangaBaka series by id. Use after "
            "search_mangabaka when you need more fields than the search "
            "result provides (description, all publishers, all secondary titles)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"series_id": {"type": "integer"}},
            "required": ["series_id"],
        },
    },
    {
        "name": "search_kavita_series",
        "description": (
            "Search the user's existing Kavita library for series matching a "
            "query. Returns simplified series entries (series_id, name, "
            "library_name). Use this to check if a similar series already lives "
            "in the library — aligning with it is preferred."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_kavita_series_metadata",
        "description": (
            "Fetch metadata for a Kavita series (publishers, release_year, "
            "language, publication_status). Use after search_kavita_series to "
            "learn what publisher + year the library has for a candidate series."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"series_id": {"type": "integer"}},
            "required": ["series_id"],
        },
    },
    {
        "name": "record_decision",
        "description": (
            "Record the final decision for this item. Terminates the turn. "
            "For 'match': issue_id is required. For 'uncertain': include "
            "review_hint to help the reviewer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["match", "uncertain"],
                },
                "source": {
                    "type": "string",
                    "enum": ["comicvine", "mangabaka"],
                    "description": (
                        "Which metadata service the issue_id belongs to. "
                        "Defaults to 'comicvine' if omitted."
                    ),
                },
                "issue_id": {
                    "type": "integer",
                    "description": (
                        "ComicVine: id of a specific issue inside the volume "
                        "(from get_comicvine_issues_for_volume). MUST differ "
                        "from volume_id. MangaBaka: same as volume_id (the "
                        "series id)."
                    ),
                },
                "volume_id": {
                    "type": "integer",
                    "description": (
                        "ComicVine: id of the volume (from search_comicvine). "
                        "MangaBaka: the series id."
                    ),
                },
                "confidence": {"type": "string", "enum": ["high", "medium"]},
                "reasoning": {"type": "string"},
                "signals_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Short labels for the signals that drove the decision, "
                        "e.g. 'sibling_consistency', 'kavita_precedent', "
                        "'source_publisher', 'cover_art', 'year_match'."
                    ),
                },
                "rejected_candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue_id": {"type": "integer"},
                            "volume_id": {"type": "integer"},
                            "reason": {"type": "string"},
                        },
                    },
                },
                "review_hint": {
                    "type": "string",
                    "description": "For uncertain decisions: what a human should check.",
                },
            },
            "required": ["decision", "reasoning"],
        },
    },
]


def build_user_text(item_context: dict[str, Any]) -> str:
    """Render the item context into the user message's text portion.

    Cover images are appended to the user message separately as image
    content blocks; this text describes everything else the agent sees.
    """
    lines = []
    lines.append(f"Lane: {item_context.get('lane', 'comics')}")
    lines.append(f"Filename: {item_context['filename']}")
    parsed = item_context.get("parsed_from_filename") or {}
    lines.append(
        f"Filename parse (best guess): series={parsed.get('raw_title')!r}, "
        f"issue={parsed.get('issue_guess')!r}"
    )
    lines.append("")
    lines.append("Source context:")
    lines.append(json.dumps(item_context.get("source_context") or {}, indent=2))
    lines.append("")
    siblings = item_context.get("sibling_items_resolved") or []
    lines.append(f"Siblings resolved so far in this source ({len(siblings)}):")
    if siblings:
        lines.append(json.dumps(siblings, indent=2))
    else:
        lines.append("(none — this is the first item of its series group)")
    lines.append("")
    kavita = item_context.get("existing_kavita_series") or []
    lines.append("Existing Kavita series matching the parsed name:")
    lines.append(json.dumps(kavita, indent=2))
    lines.append("")
    cv_candidates = item_context.get("candidates") or []
    lines.append(f"Pre-fetched ComicVine candidates ({len(cv_candidates)}):")
    lines.append(json.dumps(cv_candidates, indent=2))
    mb_candidates = item_context.get("mangabaka_candidates")
    if mb_candidates is not None:
        lines.append("")
        lines.append(f"Pre-fetched MangaBaka candidates ({len(mb_candidates)}):")
        lines.append(json.dumps(mb_candidates, indent=2))
    return "\n".join(lines)
