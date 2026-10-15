from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib import Unmeasurable, runtime


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        self.home = Path(self.env["HOME"])
        self.codex_home = Path(self.env["CODEX_HOME"])


class LeakedEnvTest(_Tmp):
    def test_codex_wins_when_claude_session_does_not_resolve(self):
        # A Codex launched from a Claude shell inherits CLAUDE_CODE_SESSION_ID,
        # but no Claude transcript exists for it here.
        helpers.install_codex_rollout(self.codex_home)
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION,
                   CODEX_THREAD_ID=helpers.CODEX_THREAD)
        r = helpers.run_ctxcheck(["--json"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["runtime"], "codex")
        self.assertEqual(out["used_tokens"], 31_200)
        self.assertEqual(out["context_window"], 258_400)

    def test_first_resolving_candidate_wins(self):
        helpers.install_claude_transcript(self.home)
        helpers.install_codex_rollout(self.codex_home)
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION,
                   CODEX_THREAD_ID=helpers.CODEX_THREAD)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(runtime.resolve().backend.name, "claude")


class NothingResolvesTest(_Tmp):
    def test_exit_3_naming_what_was_tried(self):
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION)
        r = helpers.run_ctxcheck([], env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("claude", r.stderr)
        self.assertIn("CODEX_THREAD_ID unset", r.stderr)

    def test_unmeasurable_in_process(self):
        with mock.patch.dict(os.environ, self.env, clear=True):
            with self.assertRaises(Unmeasurable) as cm:
                runtime.resolve(cwd=self.tmp)
        self.assertEqual(len(cm.exception.tried), len(runtime.candidates()))

    def test_explicit_runtime_without_data(self):
        r = helpers.run_ctxcheck(["--runtime", "codex"], self.env)
        self.assertEqual(r.returncode, 3, r.stderr)


class ExplicitTranscriptTest(_Tmp):
    def test_runtime_sniffed_from_rollout_name(self):
        path = helpers.install_codex_rollout(self.codex_home)
        r = helpers.run_ctxcheck(["--json", "--transcript", str(path)], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["runtime"], "codex")
        self.assertEqual(out["session_id"], helpers.CODEX_THREAD)

    def test_missing_transcript_is_an_error(self):
        r = helpers.run_ctxcheck(["--transcript", str(self.tmp / "nope.jsonl")], self.env)
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
