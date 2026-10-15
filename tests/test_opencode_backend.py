"""OpenCode v1/v2 backend. Every database is built synthetically in setUp under
a temp XDG_DATA_HOME; the real ~/.local/share/opencode/opencode.db is never
opened. The CREATE TABLEs mirror the columns the backend touches in
anomalyco/opencode@v2.0.29 / @v1.19.0 packages/core/src/session/sql.ts and
packages/core/src/kv/sql.ts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib.backends import opencode

V2_DDL = """
CREATE TABLE session_v2 (id text PRIMARY KEY, project_id text, parent_id text,
  directory text NOT NULL, title text, revert text, model text,
  time_created integer NOT NULL, time_updated integer NOT NULL);
CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL,
  type text NOT NULL, seq integer NOT NULL, time_created integer NOT NULL,
  time_updated integer NOT NULL, data text NOT NULL, UNIQUE(session_id, seq));
CREATE TABLE kv (key text PRIMARY KEY, value text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL);
"""

V1_DDL = """
CREATE TABLE session (id text PRIMARY KEY, project_id text, parent_id text,
  directory text NOT NULL, title text, revert text,
  time_created integer NOT NULL, time_updated integer NOT NULL);
CREATE TABLE message (id text PRIMARY KEY, session_id text NOT NULL,
  time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
CREATE TABLE part (id text PRIMARY KEY, message_id text NOT NULL,
  session_id text NOT NULL, time_created integer NOT NULL,
  time_updated integer NOT NULL, data text NOT NULL);
"""

V2_SESSION = "ses_synthetic_v2"
V1_SESSION = "ses_synthetic_v1"
MODEL = {"providerID": "synthprov", "id": "synth-model-1"}


def tokens(inp=0, out=0, reasoning=0, read=0, write=0) -> dict:
    return {"input": inp, "output": out, "reasoning": reasoning,
            "cache": {"read": read, "write": write}}


def catalog(window: int = 400_000) -> dict:
    """A models.dev api.json slice: map keys differ from ids on purpose."""
    return {"synth-key": {"id": "synthprov", "name": "Synthetic", "models": {
        "synth-model-key": {"id": "synth-model-1", "limit": {"context": window, "output": 8_000}},
        "other": {"id": "other-model", "limit": {"context": 1}},
    }}}


class _DataHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.db = Path(self.env["XDG_DATA_HOME"]) / "opencode" / "opencode.db"
        self.db.parent.mkdir(parents=True)
        self.work = self.tmp / "work"
        self.seq = 0

    def sql(self, script: str, *rows: tuple[str, tuple]) -> None:
        con = sqlite3.connect(str(self.db))
        try:
            if script:
                con.executescript(script)
            for stmt, args in rows:
                con.execute(stmt, args)
            con.commit()
        finally:
            con.close()

    # v2 -----------------------------------------------------------------
    def v2_session(self, sid=V2_SESSION, directory=None, updated=1_000,
                   parent=None, revert=None) -> None:
        self.sql("", ("INSERT INTO session_v2 (id, parent_id, directory, revert, "
                      "time_created, time_updated) VALUES (?,?,?,?,?,?)",
                      (sid, parent, str(directory or self.work),
                       json.dumps(revert) if revert else None, updated, updated)))

    def v2_msg(self, kind: str, data: dict, sid=V2_SESSION, mid=None) -> str:
        self.seq += 1
        mid = mid or f"msg_{self.seq:04d}"
        self.sql("", ("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                      (mid, sid, kind, self.seq, self.seq, self.seq, json.dumps(data))))
        return mid

    def v2_assistant(self, tok: dict, **kw) -> str:
        return self.v2_msg("assistant", {"agent": "build", "content": [], "model": MODEL,
                                         "tokens": tok, "time": {"created": 1}}, **kw)

    def kv_catalog(self, body: dict) -> None:
        value = {"updatedAt": 1, "digest": "synthetic", "body": json.dumps(body)}
        self.sql("", ("INSERT INTO kv VALUES (?,?,?,?)",
                      (opencode.CATALOG_KEY, json.dumps(value), 1, 1)))

    # v1 -----------------------------------------------------------------
    def v1_session(self, sid=V1_SESSION, directory=None, updated=1_000, parent=None) -> None:
        self.sql("", ("INSERT INTO session (id, parent_id, directory, time_created, "
                      "time_updated) VALUES (?,?,?,?,?)",
                      (sid, parent, str(directory or self.work), updated, updated)))

    def v1_msg(self, data: dict, sid=V1_SESSION) -> None:
        self.seq += 1
        self.sql("", ("INSERT INTO message VALUES (?,?,?,?,?)",
                      (f"msg_{self.seq:04d}", sid, self.seq, self.seq, json.dumps(data))))

    def v1_assistant(self, tok: dict, sid=V1_SESSION) -> None:
        self.v1_msg({"role": "assistant", "providerID": "synthprov",
                     "modelID": "synth-model-1", "tokens": tok}, sid=sid)

    def read(self, sid: str):
        return opencode.BACKEND.read(Path(f"{self.db}#{sid}"))


class V2Test(_DataHome):
    def setUp(self):
        super().setUp()
        self.sql(V2_DDL)
        self.v2_session()

    def test_five_field_sum(self):
        self.v2_msg("user", {"text": "hi"})
        self.v2_assistant(tokens(100, 20, 5, 3_000, 400))
        usage = self.read(V2_SESSION)
        self.assertEqual(usage.used, 3_525)
        self.assertEqual(usage.model, "synthprov/synth-model-1")

    def test_compaction_boundary(self):
        self.v2_assistant(tokens(90_000, 1_000))
        # A running (not yet completed) compaction is no boundary.
        self.v2_msg("compaction", {"status": "running", "reason": "auto"})
        self.assertEqual(self.read(V2_SESSION).used, 91_000)
        # A completed one is: usage before it no longer describes the context,
        # and the compaction row's own tokens are the summary request's.
        self.v2_msg("compaction", {"status": "completed", "reason": "auto",
                                   "tokens": tokens(70_000, 2_000)})
        self.assertEqual(self.read(V2_SESSION).used, 0)
        self.v2_assistant(tokens(4_000, 500))
        self.assertEqual(self.read(V2_SESSION).used, 4_500)

    def test_revert_point_hides_later_messages(self):
        self.v2_assistant(tokens(1_000, 100))
        undone = self.v2_msg("user", {"text": "undone"})
        self.v2_assistant(tokens(50_000, 100))
        self.sql("", ("UPDATE session_v2 SET revert = ? WHERE id = ?",
                      (json.dumps({"messageID": undone}), V2_SESSION)))
        self.assertEqual(self.read(V2_SESSION).used, 1_100)

    def test_kv_catalog_window(self):
        self.kv_catalog(catalog(400_000))
        self.v2_assistant(tokens(10_000))
        usage = self.read(V2_SESSION)
        self.assertEqual(usage.window, 400_000)
        self.assertEqual(usage.window_source, "opencode models.dev catalog")

    def test_unknown_model_has_no_window(self):
        self.kv_catalog({"synth-key": {"id": "synthprov", "models": {}}})
        self.v2_assistant(tokens(10_000))
        self.assertIsNone(self.read(V2_SESSION).window)

    def test_find_by_session_id(self):
        self.assertEqual(opencode.BACKEND.find(V2_SESSION), Path(f"{self.db}#{V2_SESSION}"))
        self.assertIsNone(opencode.BACKEND.find("ses_nope"))
        self.assertEqual(opencode.BACKEND.session_of(Path(f"{self.db}#{V2_SESSION}")),
                         V2_SESSION)

    def test_opened_read_only(self):
        self.v2_assistant(tokens(10_000))
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.read(V2_SESSION)
        opencode.BACKEND.fallback(self.work)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        con = opencode.connect(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("DELETE FROM session_message")
        finally:
            con.close()


class V1Test(_DataHome):
    def setUp(self):
        super().setUp()
        self.sql(V1_DDL)
        self.v1_session()

    def test_output_rule(self):
        self.v1_msg({"role": "user"})
        self.v1_assistant(tokens(20_000, 300, 0, 5_000, 100))
        # A newer assistant still streaming (output 0) is not counted yet.
        self.v1_assistant(tokens(60_000, 0))
        usage = self.read(V1_SESSION)
        self.assertEqual(usage.used, 25_400)
        self.assertEqual(usage.model, "synthprov/synth-model-1")

    def test_cwd_fallback(self):
        self.v1_session("ses_older", updated=500)
        self.v1_session("ses_elsewhere", directory=self.tmp / "elsewhere", updated=9_000)
        self.v1_session("ses_child", parent=V1_SESSION, updated=9_500)  # a subagent
        self.assertEqual(opencode.BACKEND.fallback(self.work),
                         Path(f"{self.db}#{V1_SESSION}"))
        self.assertIsNone(opencode.BACKEND.fallback(self.tmp / "nowhere"))

    def test_cache_file_window(self):
        cache = Path(self.env["XDG_CACHE_HOME"]) / "opencode" / "models.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps(catalog(128_000)), encoding="utf-8")
        self.v1_assistant(tokens(1_000, 10))
        usage = self.read(V1_SESSION)
        self.assertEqual((usage.window, usage.window_source),
                         (128_000, "opencode models.dev cache"))

    def test_no_cache_no_window(self):
        self.v1_assistant(tokens(1_000, 10))
        self.assertIsNone(self.read(V1_SESSION).window)


class MixedTest(_DataHome):
    """v2 keeps the migrated v1 tables in the same file."""

    def setUp(self):
        super().setUp()
        self.sql(V2_DDL + V1_DDL)

    def test_fallback_takes_the_newer_table_set(self):
        self.v1_session(updated=1_000)
        self.v2_session(updated=2_000)
        self.assertEqual(opencode.BACKEND.fallback(self.work),
                         Path(f"{self.db}#{V2_SESSION}"))

    def test_v1_session_in_a_v2_database(self):
        self.v1_session()
        self.v1_assistant(tokens(7_000, 7))
        self.assertEqual(self.read(V1_SESSION).used, 7_007)


class CliTest(_DataHome):
    def setUp(self):
        super().setUp()
        self.sql(V2_DDL)
        self.v2_session()
        self.v2_assistant(tokens(40_000, 1_000))

    def run_json(self, *args, **env):
        r = helpers.run_ctxcheck(["--json", *args], dict(self.env, **env), cwd=self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_catalog_window(self):
        self.kv_catalog(catalog(200_000))
        out = self.run_json(OPENCODE_SESSION_ID=V2_SESSION)
        self.assertEqual(out["runtime"], "opencode")
        self.assertEqual(out["used_tokens"], 41_000)
        self.assertEqual(out["context_window"], 200_000)
        self.assertEqual(out["transcript"], f"{self.db}#{V2_SESSION}")
        self.assertEqual(out["session_id"], V2_SESSION)

    def test_missing_catalog_falls_back_to_preset(self):
        out = self.run_json(OPENCODE_SESSION_ID=V2_SESSION)
        self.assertEqual(out["window_source"], "preset claude-1m")
        out = self.run_json(OPENCODE_SESSION_ID=V2_SESSION, CTXCHECK_PRESET="claude-200k")
        self.assertEqual(out["context_window"], 200_000)

    def test_transcript_database_plus_session(self):
        out = self.run_json("--runtime", "opencode", "--transcript", str(self.db),
                            "--session", V2_SESSION)
        self.assertEqual(out["used_tokens"], 41_000)
        r = helpers.run_ctxcheck(["--transcript", str(self.db)], self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--session", r.stderr)


if __name__ == "__main__":
    unittest.main()
