from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib import Unmeasurable, runtime
from ctxlib.backends import pi


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


OC_SESSION = "ses_detect"
PI_SESSION = "0199dddd-0000-7000-8000-00000000000d"


class _Agents(_Tmp):
    """Synthetic OpenCode (v1 + v2 tables) and Pi data for one cwd."""

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.db = Path(self.env["XDG_DATA_HOME"]) / "opencode" / "opencode.db"

    def opencode(self, table_set: str, sid: str = OC_SESSION) -> None:
        self.db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db))
        tok = {"input": 9_000, "output": 1_000, "reasoning": 0, "cache": {"read": 0, "write": 0}}
        if table_set == "v2":
            con.executescript(
                "CREATE TABLE session_v2 (id text PRIMARY KEY, parent_id text, directory text, "
                "revert text, time_updated integer);"
                "CREATE TABLE session_message (id text PRIMARY KEY, session_id text, type text, "
                "seq integer, data text);")
            con.execute("INSERT INTO session_v2 VALUES (?, NULL, ?, NULL, 1)", (sid, str(self.work)))
            con.execute("INSERT INTO session_message VALUES ('m1', ?, 'assistant', 1, ?)",
                        (sid, json.dumps({"tokens": tok})))
        else:
            con.executescript(
                "CREATE TABLE session (id text PRIMARY KEY, parent_id text, directory text, "
                "time_updated integer);"
                "CREATE TABLE message (id text PRIMARY KEY, session_id text, time_created integer, "
                "data text);")
            con.execute("INSERT INTO session VALUES (?, NULL, ?, 1)", (sid, str(self.work)))
            con.execute("INSERT INTO message VALUES ('m1', ?, 1, ?)",
                        (sid, json.dumps({"role": "assistant", "tokens": tok})))
        con.commit()
        con.close()

    def pi(self) -> Path:
        d = self.home / ".pi" / "agent" / "sessions" / pi.cwd_slug(self.work)
        d.mkdir(parents=True)
        p = d / f"2026-10-15T14-00-00-000Z_{PI_SESSION}.jsonl"
        lines = [{"type": "session", "id": PI_SESSION, "cwd": str(self.work)},
                 {"type": "message", "id": "a1", "parentId": None, "message": {
                     "role": "assistant", "stopReason": "stop", "usage": {"totalTokens": 6_000}}}]
        p.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        return p

    def detect(self, **env) -> dict:
        r = helpers.run_ctxcheck(["--json", "--window", "100000"], dict(self.env, **env),
                                 cwd=self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class OpenCodeDetectionTest(_Agents):
    def test_v2_by_session_id(self):
        self.opencode("v2")
        out = self.detect(OPENCODE_SESSION_ID=OC_SESSION, OPENCODE="1")
        self.assertEqual((out["runtime"], out["used_tokens"], out["session_id"]),
                         ("opencode", 10_000, OC_SESSION))

    def test_v1_by_cwd_when_only_opencode_is_set(self):
        self.opencode("v1")
        out = self.detect(OPENCODE="1")
        self.assertEqual((out["runtime"], out["session_id"]), ("opencode", OC_SESSION))

    def test_leaked_claude_id_falls_through_to_opencode(self):
        self.opencode("v2")
        out = self.detect(CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION,
                          OPENCODE_SESSION_ID=OC_SESSION)
        self.assertEqual(out["runtime"], "opencode")

    def test_opencode_flag_without_data_is_unmeasurable(self):
        r = helpers.run_ctxcheck([], dict(self.env, OPENCODE="1"), cwd=self.work)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("opencode", r.stderr)
        self.assertFalse(self.db.exists())  # never created by a read-only probe


class PiDetectionTest(_Agents):
    def test_by_session_file(self):
        p = self.pi()
        out = self.detect(PI_SESSION_FILE=str(p), PI_CODING_AGENT="true")
        self.assertEqual((out["runtime"], out["used_tokens"], out["session_id"]),
                         ("pi", 6_000, PI_SESSION))

    def test_by_session_id(self):
        self.pi()
        self.assertEqual(self.detect(PI_SESSION_ID=PI_SESSION)["runtime"], "pi")

    def test_by_cwd_when_only_pi_coding_agent_is_set(self):
        self.pi()
        self.assertEqual(self.detect(PI_CODING_AGENT="true")["runtime"], "pi")

    def test_pi_session_id_beats_a_leaked_opencode_flag(self):
        # A Pi launched from an OpenCode shell inherits OPENCODE=1, and an
        # OpenCode session exists for this cwd; Pi's own session id wins.
        self.opencode("v1")
        self.pi()
        out = self.detect(OPENCODE="1", PI_SESSION_ID=PI_SESSION, PI_CODING_AGENT="true")
        self.assertEqual(out["runtime"], "pi")


if __name__ == "__main__":
    unittest.main()
