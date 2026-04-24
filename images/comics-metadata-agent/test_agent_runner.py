"""Unit tests for agent_runner.

The full Claude tool-use loop is covered by a fake `claude_client` object
that simulates a deterministic response sequence. Live integration with the
real Anthropic API is verified separately when the container runs in cluster.

Run: python -m unittest test_agent_runner -v
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

from agent_runner import AgentRunner
from comicvine_client import ComicVineRateLimitError
from decision_log import DecisionLog


# ---- helpers for faking Anthropic SDK responses ---------------------------


class _Block:
    """Minimal stand-in for an anthropic message content block."""
    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


def _response(content_blocks):
    resp = MagicMock()
    resp.content = content_blocks
    return resp


def _make_cbz(path):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("01.jpg", b"\xff\xd8\xff\xe0cover-bytes")
        zf.writestr("02.jpg", b"\xff\xd8\xff\xe0page2-bytes")
    return path


def _make_decision_log():
    td = tempfile.mkdtemp()
    return DecisionLog(td, source_id="test-source"), Path(td)


# ---- dispatch tests -------------------------------------------------------


class DispatchToolTests(unittest.TestCase):
    """The dispatch function is pure: tool name + args → client call."""

    def setUp(self):
        self.kavita = MagicMock()
        self.comicvine = MagicMock()
        self.log, _ = _make_decision_log()
        self.runner = AgentRunner(
            claude_client=MagicMock(),
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )

    def test_search_comicvine_without_year_range(self):
        self.comicvine.search_volumes.return_value = [{"id": 1}]
        result = self.runner._dispatch_tool(
            "search_comicvine", {"series_name": "Foo"}
        )
        self.comicvine.search_volumes.assert_called_once_with("Foo", year_range=None)
        self.assertEqual(result, [{"id": 1}])

    def test_search_comicvine_with_year_range(self):
        self.comicvine.search_volumes.return_value = []
        self.runner._dispatch_tool(
            "search_comicvine",
            {"series_name": "Foo", "year_min": 2020, "year_max": 2025},
        )
        self.comicvine.search_volumes.assert_called_once_with(
            "Foo", year_range=(2020, 2025)
        )

    def test_get_comicvine_issue(self):
        self.comicvine.get_issue.return_value = {"id": 42}
        self.runner._dispatch_tool("get_comicvine_issue", {"issue_id": 42})
        self.comicvine.get_issue.assert_called_once_with(42)

    def test_get_comicvine_issues_for_volume(self):
        self.comicvine.get_issues_for_volume.return_value = []
        self.runner._dispatch_tool(
            "get_comicvine_issues_for_volume", {"volume_id": 796}
        )
        self.comicvine.get_issues_for_volume.assert_called_once_with(796)

    def test_search_kavita_series(self):
        self.kavita.search_series.return_value = []
        self.runner._dispatch_tool("search_kavita_series", {"query": "Radiant"})
        self.kavita.search_series.assert_called_once_with("Radiant")

    def test_get_kavita_series_metadata(self):
        self.kavita.get_series_metadata.return_value = {}
        self.runner._dispatch_tool("get_kavita_series_metadata", {"series_id": 42})
        self.kavita.get_series_metadata.assert_called_once_with(42)

    def test_unknown_tool(self):
        result = self.runner._dispatch_tool("nonsense", {})
        self.assertIn("_error", result)


# ---- safe prefetch --------------------------------------------------------


class SafePrefetchTests(unittest.TestCase):
    def setUp(self):
        self.kavita = MagicMock()
        self.comicvine = MagicMock()
        self.log, _ = _make_decision_log()
        self.runner = AgentRunner(
            claude_client=MagicMock(),
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )

    def test_kavita_exception_returned_as_error(self):
        self.kavita.search_series.side_effect = RuntimeError("boom")
        result = self.runner._safe_search_kavita("x")
        self.assertIn("_error", result)

    def test_comicvine_rate_limit_returned_as_error(self):
        self.comicvine.search_volumes.side_effect = ComicVineRateLimitError("boom")
        result = self.runner._safe_search_comicvine("x", year_range=None)
        self.assertIn("_error", result)
        self.assertIn("rate limit", result["_error"])

    def test_comicvine_invalid_year_range_ignored(self):
        # Non-tuple year_range shouldn't reach the client as garbage.
        self.comicvine.search_volumes.return_value = []
        self.runner._safe_search_comicvine("x", year_range="bogus")
        self.comicvine.search_volumes.assert_called_once_with("x", year_range=None)

    def test_comicvine_year_range_tuple_preserved(self):
        self.comicvine.search_volumes.return_value = []
        self.runner._safe_search_comicvine("x", year_range=[2020, 2025])
        self.comicvine.search_volumes.assert_called_once_with("x", year_range=(2020, 2025))


# ---- tool-use loop end-to-end (with a fake claude client) -----------------


class ToolUseLoopTests(unittest.TestCase):
    """Simulate Claude's tool-use protocol with a hand-rolled fake."""

    def setUp(self):
        self.kavita = MagicMock()
        self.kavita.search_series.return_value = []
        self.comicvine = MagicMock()
        self.comicvine.search_volumes.return_value = []
        self.log, self.log_dir = _make_decision_log()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cbz = _make_cbz(Path(self.tmp.name) / "Radiant Black, 1.cbz")

    def _make_runner(self, fake_claude, **overrides):
        kwargs = dict(
            claude_client=fake_claude,
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )
        kwargs.update(overrides)
        return AgentRunner(**kwargs)

    def test_oauth_mode_rewrites_system_and_prepends_user_text(self):
        """OAuth auth requires the constrained Claude-Code system message;
        our real prompt goes into the first user message text block."""
        from prompt import OAUTH_SYSTEM_MESSAGE, SYSTEM_PROMPT
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu", name="record_decision", input={
                "decision": "uncertain",
                "reasoning": "n/a",
            }),
        ])
        runner = self._make_runner(claude, oauth_mode=True)
        runner.run_item(self.cbz, {}, [])

        _, kwargs = claude.messages.create.call_args
        self.assertEqual(kwargs["system"], OAUTH_SYSTEM_MESSAGE)
        # First user message's first text block must contain our real prompt.
        user_content = kwargs["messages"][0]["content"]
        text_block = user_content[0]
        self.assertEqual(text_block["type"], "text")
        self.assertTrue(text_block["text"].startswith(SYSTEM_PROMPT))
        self.assertIn("Radiant Black, 1.cbz", text_block["text"])

    def test_api_key_mode_leaves_system_as_prompt(self):
        from prompt import SYSTEM_PROMPT
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu", name="record_decision", input={
                "decision": "uncertain",
                "reasoning": "n/a",
            }),
        ])
        runner = self._make_runner(claude)  # oauth_mode defaults to False
        runner.run_item(self.cbz, {}, [])

        _, kwargs = claude.messages.create.call_args
        self.assertEqual(kwargs["system"], SYSTEM_PROMPT)
        user_content = kwargs["messages"][0]["content"]
        # Real prompt must NOT be in the user text in api_key mode.
        self.assertNotIn(SYSTEM_PROMPT, user_content[0]["text"])

    def test_immediate_record_decision_match(self):
        # Claude answers on the first turn.
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu1", name="record_decision", input={
                "decision": "match",
                "issue_id": 12345,
                "volume_id": 796,
                "confidence": "high",
                "reasoning": "Cover + publisher + sibling all align.",
                "signals_used": ["cover_art", "source_publisher"],
            }),
        ])
        runner = self._make_runner(claude)
        decision = runner.run_item(self.cbz, {}, [])
        self.assertEqual(decision["decision"], "match")
        self.assertEqual(decision["issue_id"], 12345)
        self.assertEqual(decision["filename"], "Radiant Black, 1.cbz")
        # Exactly one Claude call — no tool-dispatch round trip needed.
        self.assertEqual(claude.messages.create.call_count, 1)

    def test_tool_call_then_decision(self):
        # First turn: search_comicvine. Second turn: record_decision.
        claude = MagicMock()
        claude.messages.create.side_effect = [
            _response([
                _Block("tool_use", id="tu1", name="search_comicvine",
                       input={"series_name": "Radiant Black"}),
            ]),
            _response([
                _Block("tool_use", id="tu2", name="record_decision", input={
                    "decision": "match",
                    "issue_id": 99,
                    "volume_id": 7,
                    "reasoning": "ok",
                }),
            ]),
        ]
        self.comicvine.search_volumes.return_value = [{"id": 7, "name": "Radiant Black"}]
        runner = self._make_runner(claude)
        decision = runner.run_item(self.cbz, {}, [])
        self.assertEqual(decision["decision"], "match")
        self.assertEqual(claude.messages.create.call_count, 2)

    def test_tool_error_surfaces_to_model_as_tool_result(self):
        # The ComicVine client raises on the first tool call; runner must
        # pass that as a tool_result and let the model recover on next turn.
        self.comicvine.search_volumes.side_effect = ComicVineRateLimitError("burnt")
        claude = MagicMock()
        claude.messages.create.side_effect = [
            _response([_Block("tool_use", id="tu1", name="search_comicvine",
                              input={"series_name": "x"})]),
            _response([_Block("tool_use", id="tu2", name="record_decision", input={
                "decision": "uncertain",
                "reasoning": "rate limited, giving up",
            })]),
        ]
        runner = self._make_runner(claude)
        decision = runner.run_item(self.cbz, {}, [])
        self.assertEqual(decision["decision"], "uncertain")
        # The rate-limit error must have been delivered to the model as a
        # tool_result user turn. (Can't check msgs[-1] positionally: the
        # messages list gets mutated after each create() call, and
        # MagicMock's call_args stores a reference, not a snapshot.)
        second_call_kwargs = claude.messages.create.call_args_list[1].kwargs
        msgs = second_call_kwargs["messages"]
        tool_result_turns = [
            m for m in msgs
            if m["role"] == "user"
            and isinstance(m["content"], list)
            and m["content"]
            and isinstance(m["content"][0], dict)
            and m["content"][0].get("type") == "tool_result"
        ]
        self.assertEqual(len(tool_result_turns), 1)
        content = tool_result_turns[0]["content"][0]["content"]
        self.assertIn("rate limit", content)

    def test_max_rounds_exhaustion_returns_uncertain(self):
        # Model never calls record_decision — just keeps calling search.
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu1", name="search_comicvine",
                   input={"series_name": "x"}),
        ])
        runner = self._make_runner(claude, max_tool_rounds=3)
        decision = runner.run_item(self.cbz, {}, [])
        self.assertEqual(decision["decision"], "uncertain")
        self.assertIn("Exceeded", decision["reasoning"])
        self.assertEqual(claude.messages.create.call_count, 3)

    def test_no_tool_use_returns_uncertain(self):
        # Model responded with text only — protocol failure.
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("text", text="I don't know."),
        ])
        runner = self._make_runner(claude)
        decision = runner.run_item(self.cbz, {}, [])
        self.assertEqual(decision["decision"], "uncertain")
        self.assertIn("protocol failure", decision["reasoning"])


# ---- run_group accumulates siblings --------------------------------------


class RunGroupTests(unittest.TestCase):
    def setUp(self):
        self.kavita = MagicMock()
        self.kavita.search_series.return_value = []
        self.comicvine = MagicMock()
        self.comicvine.search_volumes.return_value = []
        self.log, _ = _make_decision_log()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _make_cbzs(self, names):
        return [_make_cbz(Path(self.tmp.name) / n) for n in names]

    def test_siblings_accumulate_across_items_in_group(self):
        # Claude resolves both items to the same volume. After the first, the
        # second item's prefetch should carry a sibling record.
        claude = MagicMock()
        claude.messages.create.side_effect = [
            _response([_Block("tool_use", id=f"tu{i}", name="record_decision", input={
                "decision": "match",
                "issue_id": 100 + i,
                "volume_id": 7,
                "reasoning": "ok",
            })])
            for i in range(2)
        ]
        runner = AgentRunner(
            claude_client=claude,
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )
        paths = self._make_cbzs(["Radiant Black, 1.cbz", "Radiant Black, 2.cbz"])
        siblings = runner.run_group(paths, source_context={})
        self.assertEqual(len(siblings), 2)
        # The second Claude call must have the first item in its siblings_resolved.
        second_messages = claude.messages.create.call_args_list[1].kwargs["messages"]
        user_text = second_messages[0]["content"][0]["text"]
        self.assertIn("Radiant Black, 1.cbz", user_text)
        self.assertIn("resolved_volume_id", user_text)

    def test_uncertain_does_not_accumulate_sibling(self):
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu", name="record_decision", input={
                "decision": "uncertain",
                "reasoning": "not sure",
            })
        ])
        runner = AgentRunner(
            claude_client=claude,
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )
        paths = self._make_cbzs(["Foo, 1.cbz"])
        siblings = runner.run_group(paths, source_context={})
        self.assertEqual(siblings, [])

    def test_decisions_persist_to_log(self):
        claude = MagicMock()
        claude.messages.create.return_value = _response([
            _Block("tool_use", id="tu", name="record_decision", input={
                "decision": "match",
                "issue_id": 1,
                "volume_id": 2,
                "reasoning": "ok",
            })
        ])
        runner = AgentRunner(
            claude_client=claude,
            kavita=self.kavita,
            comicvine=self.comicvine,
            decision_log=self.log,
            model="claude-sonnet-4-6",
        )
        paths = self._make_cbzs(["Foo, 1.cbz"])
        runner.run_group(paths, source_context={})
        records = self.log.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["filename"], "Foo, 1.cbz")
        self.assertEqual(records[0]["decision"], "match")


if __name__ == "__main__":
    unittest.main()
