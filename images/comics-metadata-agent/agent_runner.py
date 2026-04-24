"""Agent tool-use loop for per-item metadata resolution.

Architecture:
  - `AgentRunner` holds the API clients + decision log and processes one
    series group at a time, accumulating sibling context as it goes.
  - Each item gets pre-fetched context (Kavita matches, ComicVine
    candidates, cover pages) seeded into the initial message.
  - Claude runs a tool-use loop, optionally calling search_* tools to
    refine, and terminates by calling `record_decision`.
  - V0 is propose-only: decisions are logged, but files stay in
    `_unmatched/`. An apply-mode pass (future) replays the log to move
    files into the library via comictagger.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from comicvine_client import ComicVineClient, ComicVineRateLimitError
from cover_extractor import extract_pages_as_anthropic_images
from decision_log import DecisionLog
from filename_parser import parse_filename
from kavita_client import KavitaClient
from prompt import OAUTH_SYSTEM_MESSAGE, SYSTEM_PROMPT, TOOLS, build_user_text


# Cover page indices we send to the model. Page 0 = front cover (always
# hi-res); page 5 = a content page that often carries series branding +
# issue numbering imagery for disambiguation. Out-of-range is silently
# tolerated by the extractor.
DEFAULT_COVER_PAGE_INDICES = [0, 5]


class AgentRunner:
    def __init__(
        self,
        claude_client,
        kavita: KavitaClient,
        comicvine: ComicVineClient,
        decision_log: DecisionLog,
        model: str,
        max_tool_rounds: int = 8,
        oauth_mode: bool = False,
    ):
        self.claude = claude_client
        self.kavita = kavita
        self.comicvine = comicvine
        self.decision_log = decision_log
        self.model = model
        self.max_tool_rounds = max_tool_rounds
        # OAuth tokens require a fixed system message + the real prompt
        # injected into the first user message. See prompt.py.
        self.oauth_mode = oauth_mode

    # ---- orchestration ----------------------------------------------------

    def run_group(
        self,
        group_paths: list[Path],
        source_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Process a series group sequentially. Returns the sibling records
        accumulated during the run (useful for callers inspecting state)."""
        siblings: list[dict[str, Any]] = []
        for path in group_paths:
            decision = self.run_item(path, source_context, siblings)
            recorded = self.decision_log.append(decision)
            if recorded["decision"] == "match" and recorded.get("volume_id"):
                siblings.append({
                    "filename": path.name,
                    "resolved_volume_id": recorded["volume_id"],
                    "resolved_issue_id": recorded.get("issue_id"),
                })
        return siblings

    def run_item(
        self,
        cbz_path: Path,
        source_context: dict[str, Any],
        siblings_resolved: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the tool-use loop for a single item. Returns the decision
        dict ready to be fed into DecisionLog.append()."""
        parsed = parse_filename(cbz_path.name)

        kavita_matches = self._safe_search_kavita(parsed["raw_title"])
        cv_candidates = self._safe_search_comicvine(
            parsed["raw_title"],
            source_context.get("inferred_year_range"),
        )
        cover_images = self._safe_extract_covers(cbz_path)

        item_context = {
            "filename": cbz_path.name,
            "parsed_from_filename": parsed,
            "source_context": source_context,
            "sibling_items_resolved": siblings_resolved,
            "existing_kavita_series": kavita_matches,
            "candidates": cv_candidates,
        }

        decision = self._run_tool_use_loop(item_context, cover_images)
        decision["filename"] = cbz_path.name
        return decision

    # ---- pre-fetch helpers -------------------------------------------------

    def _safe_search_kavita(self, name: str) -> Any:
        try:
            return self.kavita.search_series(name)
        except Exception as e:
            return {"_error": f"kavita search failed: {e}"}

    def _safe_search_comicvine(
        self,
        name: str,
        year_range: Any,
    ) -> Any:
        try:
            yr = None
            if (
                isinstance(year_range, (list, tuple))
                and len(year_range) == 2
                and all(isinstance(v, int) for v in year_range)
            ):
                yr = (year_range[0], year_range[1])
            return self.comicvine.search_volumes(name, year_range=yr)
        except ComicVineRateLimitError as e:
            return {"_error": f"comicvine rate limit: {e}"}
        except Exception as e:
            return {"_error": f"comicvine search failed: {e}"}

    def _safe_extract_covers(self, cbz_path: Path) -> list[dict]:
        try:
            return extract_pages_as_anthropic_images(
                cbz_path, DEFAULT_COVER_PAGE_INDICES
            )
        except Exception:
            return []

    # ---- tool-use loop -----------------------------------------------------

    def _run_tool_use_loop(
        self,
        item_context: dict[str, Any],
        cover_images: list[dict],
    ) -> dict[str, Any]:
        user_text = build_user_text(item_context)
        if self.oauth_mode:
            # OAuth auth: system must be the constrained Claude-Code string
            # and our real prompt prepends the user text.
            system_message = OAUTH_SYSTEM_MESSAGE
            user_text = f"{SYSTEM_PROMPT}\n\n---\n\n{user_text}"
        else:
            system_message = SYSTEM_PROMPT

        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    *cover_images,
                ],
            }
        ]

        for _ in range(self.max_tool_rounds):
            response = self.claude.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_message,
                tools=TOOLS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [
                block for block in response.content
                if getattr(block, "type", None) == "tool_use"
            ]

            for tu in tool_uses:
                if tu.name == "record_decision":
                    return dict(tu.input)

            if not tool_uses:
                return {
                    "decision": "uncertain",
                    "reasoning": "Agent produced no tool_use block (protocol failure).",
                }

            tool_results = []
            for tu in tool_uses:
                try:
                    result = self._dispatch_tool(tu.name, dict(tu.input))
                except ComicVineRateLimitError as e:
                    result = {"_error": f"comicvine rate limit: {e}"}
                except Exception as e:
                    result = {"_error": f"{type(e).__name__}: {e}"}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str)[:20000],
                })
            messages.append({"role": "user", "content": tool_results})

        return {
            "decision": "uncertain",
            "reasoning": (
                f"Exceeded {self.max_tool_rounds} tool-use rounds without "
                "calling record_decision."
            ),
        }

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> Any:
        if name == "search_comicvine":
            yr_min = args.get("year_min")
            yr_max = args.get("year_max")
            yr = (yr_min, yr_max) if yr_min is not None and yr_max is not None else None
            return self.comicvine.search_volumes(args["series_name"], year_range=yr)
        if name == "get_comicvine_issue":
            return self.comicvine.get_issue(args["issue_id"])
        if name == "get_comicvine_issues_for_volume":
            return self.comicvine.get_issues_for_volume(args["volume_id"])
        if name == "search_kavita_series":
            return self.kavita.search_series(args["query"])
        if name == "get_kavita_series_metadata":
            return self.kavita.get_series_metadata(args["series_id"])
        return {"_error": f"unknown tool: {name}"}
