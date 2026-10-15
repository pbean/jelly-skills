"""Pi backend. Session trees are synthetic, written under a temp HOME; the
shapes follow earendil-works/pi@v1.2.0 docs/session-format.md."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib.backends import opencode, pi

PI_SESSION = "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
PROVIDER, MODEL = "synthprov", "synth-model-1"


def header(sid: str = PI_SESSION, cwd: str = "/synthetic/work") -> dict:
    return {"type": "session", "version": 3, "id": sid,
            "timestamp": "2026-10-15T14:00:00.000Z", "cwd": cwd}


def msg(eid: str, parent: str | None, role: str = "user", usage: dict | None = None,
        stop: str = "stop") -> dict:
    m: dict = {"role": role, "content": []}
    if role == "assistant":
        m.update(provider=PROVIDER, model=MODEL, stopReason=stop,
                 usage=usage or {})
    return {"type": "message", "id": eid, "parentId": parent,
            "timestamp": "2026-10-15T14:00:00.000Z", "message": m}


def usage(inp=0, out=0, read=0, write=0, total=None) -> dict:
    u = {"input": inp, "output": out, "cacheRead": read, "cacheWrite": write}
    if total is not None:
        u["totalTokens"] = total
    return u


class _PiHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agent = Path(self.env["HOME"]) / ".pi" / "agent"
        self.work = self.tmp / "work"

    def write(self, entries: list[dict], sid: str = PI_SESSION, cwd: Path | None = None,
              root: Path | None = None, stamp: str = "2026-10-15T14-00-00-000Z") -> Path:
        cwd = cwd or self.work
        d = root or (self.agent / "sessions" / pi.cwd_slug(cwd))
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{stamp}_{sid}.jsonl"
        lines = [header(sid, str(cwd))] + entries
        p.write_text("".join(json.dumps(e) + "\n" for e in lines) + '{"type":"mess',
                     encoding="utf-8")  # a partially flushed tail line
        return p


class TreeTest(_PiHome):
    def test_abandoned_sibling_branch_is_ignored(self):
        p = self.write([
            msg("u1", None),
            msg("a1", "u1", "assistant", usage(total=1_000)),
            # /tree went back to a1 and retried: this branch is abandoned.
            msg("u2x", "a1"),
            msg("a2x", "u2x", "assistant", usage(total=99_000)),
            # The live branch; its last entry is the leaf.
            msg("u2", "a1"),
            msg("a2", "u2", "assistant", usage(total=2_000)),
            msg("u3", "a2"),
        ])
        u = pi.BACKEND.read(p)
        self.assertEqual(u.used, 2_000)
        self.assertEqual(u.model, f"{PROVIDER}/{MODEL}")

    def test_compaction_resets_until_the_next_response(self):
        entries = [
            msg("u1", None),
            msg("a1", "u1", "assistant", usage(total=150_000)),
            {"type": "compaction", "id": "c1", "parentId": "a1", "summary": "...",
             "firstKeptEntryId": "u1", "tokensBefore": 150_000,
             "usage": usage(total=9_000)},
        ]
        self.assertEqual(pi.BACKEND.read(self.write(entries)).used, 0)
        entries += [msg("u2", "c1"), msg("a2", "u2", "assistant", usage(total=12_000))]
        self.assertEqual(pi.BACKEND.read(self.write(entries)).used, 12_000)

    def test_compaction_on_an_abandoned_branch_does_not_count(self):
        p = self.write([
            msg("u1", None),
            msg("a1", "u1", "assistant", usage(total=30_000)),
            {"type": "compaction", "id": "c1", "parentId": "a1", "summary": "..."},
            msg("u2", "a1"),
        ])
        self.assertEqual(pi.BACKEND.read(p).used, 30_000)

    def test_aborted_and_error_assistants_are_skipped(self):
        p = self.write([
            msg("u1", None),
            msg("a1", "u1", "assistant", usage(total=4_000)),
            msg("u2", "a1"),
            msg("a2", "u2", "assistant", usage(total=9_000), stop="aborted"),
            msg("a3", "a2", "assistant", usage(total=8_000), stop="error"),
            msg("a4", "a3", "assistant", usage()),  # zero usage says nothing
        ])
        self.assertEqual(pi.BACKEND.read(p).used, 4_000)

    def test_total_tokens_vs_field_sum(self):
        p = self.write([msg("a1", None, "assistant", usage(10, 20, 300, 4_000, total=5_555))])
        self.assertEqual(pi.BACKEND.read(p).used, 5_555)
        p = self.write([msg("a1", None, "assistant", usage(10, 20, 300, 4_000))])
        self.assertEqual(pi.BACKEND.read(p).used, 4_330)
        p = self.write([msg("a1", None, "assistant", usage(10, 20, 300, 4_000, total=0))])
        self.assertEqual(pi.BACKEND.read(p).used, 4_330)


class LocateTest(_PiHome):
    def test_cwd_slug(self):
        self.assertEqual(pi.cwd_slug(Path("/home/u/dev/proj")), "--home-u-dev-proj--")

    def test_find_by_id_and_file(self):
        p = self.write([msg("u1", None)])
        self.assertEqual(pi.BACKEND.find(PI_SESSION), p)
        self.assertEqual(pi.BACKEND.session_of(p), PI_SESSION)
        os.environ["PI_SESSION_FILE"] = str(p)
        self.assertEqual(pi.BACKEND.session_id(), PI_SESSION)
        self.assertIsNone(pi.BACKEND.find("not-a-session"))

    def test_fallback_newest_in_cwd_dir(self):
        old = self.write([msg("u1", None)], sid="old-session")
        new = self.write([msg("u1", None)], sid="new-session")
        self.write([msg("u1", None)], sid="other-cwd", cwd=self.tmp / "elsewhere")
        os.utime(old, (1_000, 1_000))
        os.utime(new, (2_000, 2_000))
        self.assertEqual(pi.BACKEND.fallback(self.work), new)

    def test_fallback_custom_session_dir_matches_header_cwd(self):
        flat = self.tmp / "flat"
        os.environ["PI_CODING_AGENT_SESSION_DIR"] = str(flat)
        mine = self.write([msg("u1", None)], sid="mine", root=flat)
        other = self.write([msg("u1", None)], sid="other", cwd=self.tmp / "elsewhere", root=flat)
        os.utime(mine, (1_000, 1_000))
        os.utime(other, (2_000, 2_000))
        self.assertEqual(pi.BACKEND.fallback(self.work), mine)
        self.assertEqual(pi.BACKEND.find("other"), other)


class WindowTest(_PiHome):
    """PI_PROVIDER/PI_MODEL -> Pi's models.json -> models-store.json ->
    OpenCode's models.dev data -> nothing (the CLI then needs --window)."""

    def setUp(self):
        super().setUp()
        self.agent.mkdir(parents=True)
        self.path = self.write([msg("a1", None, "assistant", usage(total=50_000))])

    def models_json(self, window: int) -> None:
        (self.agent / "models.json").write_text(json.dumps({"providers": {PROVIDER: {
            "models": [{"id": MODEL, "contextWindow": window}]}}}), encoding="utf-8")

    def models_store(self, window: int) -> None:
        (self.agent / "models-store.json").write_text(json.dumps({PROVIDER: {
            "models": [{"id": MODEL, "contextWindow": window}], "checkedAt": 1}}),
            encoding="utf-8")

    def opencode_kv(self, window: int) -> None:
        db = Path(self.env["XDG_DATA_HOME"]) / "opencode" / "opencode.db"
        db.parent.mkdir(parents=True)
        body = {PROVIDER: {"id": PROVIDER, "models": {MODEL: {
            "id": MODEL, "limit": {"context": window}}}}}
        con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE kv (key text PRIMARY KEY, value text NOT NULL)")
        con.execute("INSERT INTO kv VALUES (?, ?)", (opencode.CATALOG_KEY,
                    json.dumps({"updatedAt": 1, "body": json.dumps(body)})))
        con.commit()
        con.close()

    def window(self):
        u = pi.BACKEND.read(self.path)
        return u.window, u.window_source

    def test_resolution_order(self):
        self.assertEqual(self.window(), (None, None))
        self.opencode_kv(300_000)
        self.assertEqual(self.window(), (300_000, "opencode models.dev catalog"))
        self.models_store(272_000)
        self.assertEqual(self.window(), (272_000, "pi models-store.json"))
        self.models_json(64_000)
        self.assertEqual(self.window(), (64_000, "pi models.json"))

    def test_env_model_wins_over_the_transcript(self):
        (self.agent / "models-store.json").write_text(json.dumps({"otherprov": {
            "models": [{"id": "other-model", "contextWindow": 32_000}]}}), encoding="utf-8")
        os.environ.update(PI_PROVIDER="otherprov", PI_MODEL="other-model")
        self.assertEqual(self.window(), (32_000, "pi models-store.json"))

    def run_cli(self, *args, **env):
        return helpers.run_ctxcheck(["--json", *args],
                                    dict(self.env, PI_SESSION_ID=PI_SESSION, **env))

    def test_cli_unknown_window_exits_3_naming_window(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("--window", r.stderr)
        self.assertIn("CTXCHECK_WINDOW", r.stderr)
        self.assertEqual(json.loads(r.stdout)["runtime"], "pi")

    def test_cli_explicit_window_sources(self):
        for args, env, want in ((["--window", "100000"], {}, 100_000),
                                ([], {"CTXCHECK_WINDOW": "80000"}, 80_000),
                                ([], {"CTXCHECK_PRESET": "claude-200k"}, 200_000)):
            r = self.run_cli(*args, **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual((out["runtime"], out["used_tokens"], out["context_window"]),
                             ("pi", 50_000, want))

    def test_cli_catalog_window(self):
        self.models_store(200_000)
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["used_percentage"], 25.0)


if __name__ == "__main__":
    unittest.main()
