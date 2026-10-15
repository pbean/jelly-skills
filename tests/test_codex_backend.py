from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib.backends import codex

SUBAGENT = "019a0000-0000-7000-8000-0000000000aa"
OTHER = "019a0000-0000-7000-8000-0000000000bb"


class _CodexHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        env = helpers.clean_env(self.tmp)
        self.codex_home = Path(env["CODEX_HOME"])
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ReadUsageTest(_CodexHome):
    def test_total_tokens_is_used(self):
        path = helpers.install_codex_rollout(self.codex_home)
        usage = codex.BACKEND.read(path)
        # last_token_usage.total_tokens (31,200), not input_tokens (30,000) as
        # the old ctxcheck read, and never the cumulative total_token_usage.
        self.assertEqual(usage.used, 31_200)
        self.assertEqual(usage.model, "gpt-synthetic-1")

    def test_window_from_the_event(self):
        path = helpers.install_codex_rollout(self.codex_home)
        usage = codex.BACKEND.read(path)
        self.assertEqual(usage.window, 258_400)
        self.assertEqual(usage.window_source, "session")

    def test_null_info_only(self):
        p = self.tmp / "rollout-x.jsonl"
        p.write_text('{"type":"event_msg","payload":{"type":"token_count","info":null}}\n',
                     encoding="utf-8")
        self.assertEqual(codex.BACKEND.read(p)[:2], (0, None))


class FindTest(_CodexHome):
    def test_find_by_thread(self):
        path = helpers.install_codex_rollout(self.codex_home)
        self.assertEqual(codex.BACKEND.find(helpers.CODEX_THREAD), path)
        self.assertEqual(codex.BACKEND.session_of(path), helpers.CODEX_THREAD)
        self.assertIsNone(codex.BACKEND.find(OTHER))

    def test_find_reverted_thread(self):
        path = helpers.install_codex_rollout(self.codex_home,
                                             thread=f"{helpers.CODEX_THREAD}_r2")
        self.assertEqual(codex.BACKEND.find(helpers.CODEX_THREAD), path)
        self.assertEqual(codex.BACKEND.session_of(path), helpers.CODEX_THREAD)


class CwdFallbackTest(_CodexHome):
    def test_newest_root_rollout_in_cwd(self):
        work = self.tmp / "work"
        want = helpers.install_codex_rollout(self.codex_home, cwd=work,
                                             stamp="2026-10-15T09-00-00")
        helpers.install_codex_rollout(self.codex_home, thread=OTHER, cwd=self.tmp / "elsewhere",
                                      stamp="2026-10-15T10-00-00")
        sub = helpers.install_codex_rollout(self.codex_home, thread=SUBAGENT, cwd=work,
                                            stamp="2026-10-15T11-00-00",
                                            session=helpers.CODEX_THREAD)
        os.utime(want, (1_000, 1_000))
        os.utime(sub, (3_000, 3_000))
        self.assertEqual(codex.BACKEND.fallback(work), want)

    def test_nothing_in_cwd(self):
        helpers.install_codex_rollout(self.codex_home, cwd=self.tmp / "elsewhere")
        self.assertIsNone(codex.BACKEND.fallback(self.tmp / "work"))


if __name__ == "__main__":
    unittest.main()
