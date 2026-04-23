"""Unit tests for agent.py helpers. The main() orchestration is covered
by integration testing (live run against _unmatched/) — too many moving
parts to usefully mock here."""

import unittest

from agent import _detect_auth_mode


class DetectAuthModeTests(unittest.TestCase):
    def test_oauth_token_prefix(self):
        self.assertEqual(
            _detect_auth_mode("sk-ant-oat01-aBcDeFgHiJkLmNoP"),
            "oauth",
        )

    def test_api_key_prefix(self):
        self.assertEqual(
            _detect_auth_mode("sk-ant-api03-aBcDeFgHiJkLmNoP"),
            "api_key",
        )

    def test_unknown_prefix_defaults_to_api_key(self):
        # Unknown format — let the SDK validate on the first request.
        self.assertEqual(_detect_auth_mode("weird-format-token"), "api_key")

    def test_empty_string_defaults_to_api_key(self):
        self.assertEqual(_detect_auth_mode(""), "api_key")


if __name__ == "__main__":
    unittest.main()
