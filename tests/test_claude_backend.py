from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib.backends import claude

FIXTURE = helpers.FIXTURES / "claude" / "session-basic.jsonl"


class ReadUsageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, helpers.clean_env(self.tmp), clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_three_field_sum_of_last_main_turn(self):
        usage = claude.BACKEND.read(FIXTURE)
        # 1000 input + 2000 cache_creation + 40000 cache_read; output excluded.
        self.assertEqual(usage.used, 43_000)
        self.assertEqual(usage.model, "claude-opus-5-5")

    def test_sidechain_and_synthetic_skipped(self):
        # The fixture's later sidechain (900K) and <synthetic> (0) records and
        # malformed tail line must not displace the last main-thread turn.
        usage = claude.BACKEND.read(FIXTURE)
        self.assertNotEqual(usage.used, 900_000)
        self.assertNotEqual(usage.used, 0)

    def test_empty_transcript_reads_zero(self):
        p = self.tmp / "empty.jsonl"
        p.write_text("not json\n", encoding="utf-8")
        usage = claude.BACKEND.read(p)
        self.assertEqual((usage.used, usage.window, usage.model), (0, None, None))

    def test_find_and_session_of(self):
        path = helpers.install_claude_transcript(self.tmp / "home")
        found = claude.BACKEND.find(helpers.CLAUDE_SESSION)
        self.assertEqual(found, path)
        self.assertEqual(claude.BACKEND.session_of(found), helpers.CLAUDE_SESSION)
        self.assertIsNone(claude.BACKEND.find("no-such-session"))


class WindowRulesTest(unittest.TestCase):
    def w(self, model, **env):
        return claude.window_for(model, env)[0]

    def test_1m_by_default_models(self):
        for model in ("claude-opus-5-5", "claude-opus-4-7", "claude-opus-4-8",
                      "claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5",
                      "claude-fable-5", "claude-fable-5-1",
                      "us.anthropic.claude-opus-4-8-v1:0"):
            with self.subTest(model=model):
                self.assertEqual(self.w(model), 1_000_000)

    def test_200k_models(self):
        for model in ("claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                      "claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-5",
                      "claude-3-5-sonnet-20241022"):
            with self.subTest(model=model):
                self.assertEqual(self.w(model), 200_000)

    def test_1m_suffix(self):
        self.assertEqual(self.w("claude-sonnet-4-6[1m]"), 1_000_000)

    def test_unrecognized_id_is_200k(self):
        self.assertEqual(claude.window_for("my-gateway-model", {}),
                         (200_000, "model rule (unrecognized id)"))

    def test_disable_1m(self):
        self.assertEqual(claude.window_for("claude-opus-5-5",
                                           {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}),
                         (200_000, "CLAUDE_CODE_DISABLE_1M_CONTEXT"))

    def test_max_context_tokens_for_unrecognized_id(self):
        self.assertEqual(claude.window_for("my-gateway-model",
                                           {"CLAUDE_CODE_MAX_CONTEXT_TOKENS": "128000"}),
                         (128_000, "CLAUDE_CODE_MAX_CONTEXT_TOKENS"))

    def test_max_context_tokens_ignored_for_recognized_id(self):
        self.assertEqual(self.w("claude-opus-5-5", CLAUDE_CODE_MAX_CONTEXT_TOKENS="128000"),
                         1_000_000)

    def test_max_context_tokens_with_disable_compact(self):
        self.assertEqual(self.w("claude-opus-5-5", CLAUDE_CODE_MAX_CONTEXT_TOKENS="128000",
                                DISABLE_COMPACT="1"), 128_000)

    def test_unknown_model_leaves_window_to_presets(self):
        self.assertEqual(claude.window_for(None, {}), (None, None))

    def test_read_applies_rules_from_env(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}):
            self.assertEqual(claude.BACKEND.read(FIXTURE).window, 200_000)


if __name__ == "__main__":
    unittest.main()
