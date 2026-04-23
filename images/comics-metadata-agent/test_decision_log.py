"""Unit tests for decision_log.

Run: python -m unittest test_decision_log -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from decision_log import DecisionLog, DecisionSchemaError


class DecisionLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = DecisionLog(self.tmp.name, source_id="S4NqZxAkmRkKZmEt")

    def test_log_dir_created_if_missing(self):
        nested = Path(self.tmp.name) / "a" / "b" / "c"
        DecisionLog(nested, source_id="k")
        self.assertTrue(nested.is_dir())

    def test_append_match(self):
        rec = self.log.append({
            "filename": "Radiant Black, 1.cbz",
            "decision": "match",
            "issue_id": 12345,
            "volume_id": 796,
            "confidence": "high",
            "reasoning": "Cover matches, publisher + year align.",
            "signals_used": ["cover_art", "source_publisher"],
        })
        self.assertEqual(rec["source_id"], "S4NqZxAkmRkKZmEt")
        self.assertIn("when", rec)
        self.assertTrue(rec["when"].endswith("+00:00"))

    def test_append_uncertain(self):
        rec = self.log.append({
            "filename": "Rogue Sun, 3.cbz",
            "decision": "uncertain",
            "reasoning": "Two plausible candidates; cover art inconclusive.",
            "review_hint": "Check year against source purchase date.",
        })
        self.assertEqual(rec["decision"], "uncertain")
        # Uncertain decisions don't require issue_id.
        self.assertNotIn("issue_id", rec)

    def test_file_is_jsonl(self):
        self.log.append({
            "filename": "a.cbz",
            "decision": "uncertain",
            "reasoning": "foo",
        })
        self.log.append({
            "filename": "b.cbz",
            "decision": "match",
            "issue_id": 1,
            "reasoning": "bar",
        })
        lines = self.log.path.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        # Each line is independently parseable.
        for line in lines:
            json.loads(line)

    def test_append_does_not_mutate_caller_dict(self):
        d = {
            "filename": "a.cbz",
            "decision": "uncertain",
            "reasoning": "foo",
        }
        before = dict(d)
        self.log.append(d)
        self.assertEqual(d, before)

    def test_missing_required_field_raises(self):
        with self.assertRaises(DecisionSchemaError):
            self.log.append({"decision": "match", "reasoning": "x"})  # no filename
        with self.assertRaises(DecisionSchemaError):
            self.log.append({"filename": "a.cbz", "decision": "match"})  # no reasoning

    def test_invalid_decision_value_raises(self):
        with self.assertRaises(DecisionSchemaError):
            self.log.append({
                "filename": "a.cbz",
                "decision": "maybe",
                "reasoning": "x",
            })

    def test_match_without_issue_id_raises(self):
        with self.assertRaises(DecisionSchemaError):
            self.log.append({
                "filename": "a.cbz",
                "decision": "match",
                "reasoning": "x",
            })

    def test_invalid_confidence_raises(self):
        with self.assertRaises(DecisionSchemaError):
            self.log.append({
                "filename": "a.cbz",
                "decision": "match",
                "issue_id": 1,
                "confidence": "totally",
                "reasoning": "x",
            })

    def test_read_all_roundtrip(self):
        self.log.append({"filename": "a.cbz", "decision": "match", "issue_id": 1, "reasoning": "x"})
        self.log.append({"filename": "b.cbz", "decision": "uncertain", "reasoning": "y"})
        records = self.log.read_all()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["filename"], "a.cbz")
        self.assertEqual(records[1]["decision"], "uncertain")

    def test_read_all_on_missing_file(self):
        # Fresh log, no writes yet.
        empty = DecisionLog(self.tmp.name, source_id="brand-new")
        self.assertEqual(empty.read_all(), [])

    def test_separate_sources_separate_files(self):
        a = DecisionLog(self.tmp.name, source_id="source-A")
        b = DecisionLog(self.tmp.name, source_id="source-B")
        a.append({"filename": "x.cbz", "decision": "uncertain", "reasoning": "q"})
        b.append({"filename": "y.cbz", "decision": "uncertain", "reasoning": "r"})
        self.assertEqual(len(a.read_all()), 1)
        self.assertEqual(len(b.read_all()), 1)


if __name__ == "__main__":
    unittest.main()
