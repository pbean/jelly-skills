"""statusline-tee and the Copilot / Antigravity / Cursor sidecar readers.

Payload fixtures are synthetic, shaped after the documented (Antigravity),
bundle-derived (Copilot) and community-sourced (Cursor) payloads; see
ctxlib/backends/statusline.py."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib import Unmeasurable, runtime, tee
from ctxlib.backends import statusline

SESSION = "0d000000-0000-4000-8000-00000000a9a9"


def payload(runtime_name: str, cwd: str | Path = "/synthetic/project",
            session: str = SESSION) -> str:
    text = (helpers.FIXTURES / "statusline" / f"{runtime_name}.json").read_text(encoding="utf-8")
    return text.replace("__SESSION__", session).replace("__CWD__", str(cwd))


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        self.state = Path(self.env["CTXCHECK_HOME"]) / "statusline"
        self.work = self.tmp / "work"
        self.work.mkdir()

    def tee(self, args, stdin, env=None):
        return helpers.run_ctxcheck(["statusline-tee", *args], env or self.env,
                                    cwd=self.work, stdin=stdin)

    def snapshot(self, runtime_name, text, age=0.0):
        """Write a sidecar in-process, then backdate it by `age` seconds."""
        with mock.patch.dict(os.environ, self.env, clear=True):
            path = tee.snapshot(runtime_name, text.encode())
        if age:
            snap = json.loads(path.read_text())
            snap["received_at"] -= age
            path.write_text(json.dumps(snap))
        return path


class TeeTest(_Tmp):
    def test_writes_sidecar_and_relays_stdout_and_exit_code(self):
        data = payload("antigravity", self.work)
        r = self.tee(["--runtime", "antigravity", "--", "sh", "-c",
                      "cat >/dev/null; echo my status; exit 3"], data)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(r.stdout, "my status\n")
        snap = json.loads((self.state / "antigravity" / f"{SESSION}.json").read_text())
        self.assertEqual(snap["payload"], json.loads(data))
        self.assertEqual(snap["cwd"], str(self.work))
        self.assertEqual(snap["session"], SESSION)
        self.assertLess(abs(time.time() - snap["received_at"]), 60)
        leftovers = [p.name for p in (self.state / "antigravity").iterdir()]
        self.assertEqual(leftovers, [f"{SESSION}.json"])  # temp file renamed away

    def test_single_argument_is_a_shell_command_fed_the_same_stdin(self):
        data = payload("copilot")
        r = self.tee(["--runtime", "copilot", "--", "cat | wc -c"], data)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(int(r.stdout.strip()), len(data.encode()))

    def test_malformed_stdin_still_relays(self):
        r = self.tee(["--runtime", "cursor", "--", "cat"], "not json {")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "not json {")
        self.assertFalse(self.state.exists())

    def test_bad_arguments_still_run_the_wrapped_command(self):
        for args in (["--", "echo", "hi"], ["--runtime", "../etc", "--", "echo", "hi"],
                     ["--bogus", "--", "echo", "hi"]):
            with self.subTest(args=args):
                r = self.tee(args, payload("cursor"))
                self.assertEqual((r.returncode, r.stdout), (0, "hi\n"))
        self.assertFalse(self.state.exists())

    def test_unwritable_state_dir_still_relays(self):
        blocker = self.tmp / "file-not-dir"
        blocker.write_text("")
        env = dict(self.env, CTXCHECK_HOME=str(blocker))
        r = self.tee(["--runtime", "copilot", "--", "echo", "ok"], payload("copilot"), env)
        self.assertEqual((r.returncode, r.stdout), (0, "ok\n"))

    def test_missing_wrapped_command_falls_back(self):
        r = self.tee(["--runtime", "copilot", "--", "/nonexistent/statusline"],
                     payload("copilot"))
        self.assertNotEqual(r.returncode, 0)  # the shell's 127
        r = self.tee(["--runtime", "copilot", "--", "/nonexistent/a", "b"], payload("copilot"))
        self.assertEqual(r.returncode, 127)
        self.assertEqual(r.stdout, "ctx 38%\n")

    def test_no_wrapped_command_prints_percentage(self):
        r = self.tee(["--runtime", "antigravity"], payload("antigravity"))
        self.assertEqual((r.returncode, r.stdout), (0, "ctx 12%\n"))
        r = self.tee(["--runtime", "cursor"], "[]")
        self.assertEqual((r.returncode, r.stdout), (0, "ctx --%\n"))

    def test_session_key_spellings_and_unsafe_ids(self):
        cases = ({"sessionId": "abc"}, {"conversation_id": "abc"},
                 {"session_id": "../../evil"})
        for body, name in zip(cases, ("abc", "abc", ".._.._evil")):
            with self.subTest(body=body):
                path = self.snapshot("copilot", json.dumps(dict(body, cwd="/x")))
                self.assertEqual(path, self.state / "copilot" / f"{name.lstrip('.')}.json")

    def test_payload_without_session_gets_a_per_cwd_file(self):
        path = self.snapshot("cursor", json.dumps({"cwd": "/a/b", "context_window": {}}))
        self.assertEqual(path.name, "cwd-a_b.json")


class ExtractTest(unittest.TestCase):
    def test_per_runtime_payloads(self):
        want = {
            # current_usage input + cache_creation + cache_read; size as given
            "antigravity": (125_000, 1_000_000, "synthetic-model-ag"),
            # current_context_tokens over displayed_context_limit
            "copilot": (61_000, 160_000, "synthetic-model-cp"),
            # current_usage null: used_percentage x context_window_size
            "cursor": (68_000, 272_000, "synthetic-model-cu"),
        }
        for name, expected in want.items():
            with self.subTest(runtime=name):
                self.assertEqual(statusline.extract(json.loads(payload(name))), expected)

    def test_percentage_only(self):
        body = {"context_window": {"context_window_size": 200_000, "used_percentage": 12.5}}
        self.assertEqual(statusline.extract(body), (25_000, 200_000, None))

    def test_camel_case_and_totals_ignored(self):
        body = {"contextWindow": {"contextWindowSize": 100, "totalInputTokens": 999,
                                  "currentUsage": {"inputTokens": 7, "cacheReadInputTokens": 3}},
                "model": "m"}
        self.assertEqual(statusline.extract(body), (10, 100, "m"))

    def test_nothing_usable(self):
        self.assertEqual(statusline.extract({"context_window": {"used_percentage": 50}}),
                         (None, None, None))
        self.assertEqual(statusline.extract("junk"), (None, None, None))


class ReaderTest(_Tmp):
    def check(self, extra_env, args=(), cwd=None):
        r = helpers.run_ctxcheck(["--json", *args], dict(self.env, **extra_env),
                                 cwd=cwd or self.work)
        return r, (json.loads(r.stdout) if r.stdout.strip() else None)

    def test_copilot_by_session_env(self):
        self.snapshot("copilot", payload("copilot", self.work))
        r, out = self.check({"COPILOT_AGENT_SESSION_ID": SESSION})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out["runtime"], out["used_tokens"], out["context_window"]),
                         ("copilot", 61_000, 160_000))
        self.assertEqual(out["measure_source"], "statusline")
        self.assertEqual(out["window_source"], "status line")
        self.assertEqual(out["session_id"], SESSION)
        self.assertIsInstance(out["stale_seconds"], (int, float))
        self.assertLess(out["stale_seconds"], 60)

    def test_antigravity_by_conversation_env(self):
        self.snapshot("antigravity", payload("antigravity", self.work))
        r, out = self.check({"ANTIGRAVITY_CONVERSATION_ID": SESSION})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out["runtime"], out["used_tokens"], out["context_window"]),
                         ("antigravity", 125_000, 1_000_000))

    def test_antigravity_last_conversations_fallback(self):
        other = "0d000000-0000-4000-8000-0000000000b2"
        self.snapshot("antigravity", payload("antigravity", "/elsewhere", session=other))
        # The index points this workspace at SESSION, whose snapshot records
        # a different cwd: the index wins over the cwd match.
        self.snapshot("antigravity", payload("antigravity", "/recorded/elsewhere"))
        index = Path(self.env["HOME"]) / ".gemini" / "antigravity-cli" / "cache"
        index.mkdir(parents=True)
        (index / "last_conversations.json").write_text(
            json.dumps({"/unrelated": other, str(self.work): SESSION}, indent=2))
        r, out = self.check({}, ["--runtime", "antigravity"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out["session_id"], SESSION)

    def test_cursor_newest_sidecar_for_cwd(self):
        old = self.snapshot("cursor", payload("cursor", self.work, session="old-one"))
        os.utime(old, (time.time() - 100, time.time() - 100))
        self.snapshot("cursor", payload("cursor", self.tmp / "other-dir", session="other"))
        self.snapshot("cursor", payload("cursor", self.work))
        r, out = self.check({"CURSOR_AGENT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out["runtime"], out["session_id"], out["used_tokens"]),
                         ("cursor", SESSION, 68_000))

    def test_cursor_ignores_other_directories(self):
        self.snapshot("cursor", payload("cursor", self.tmp / "other-dir"))
        r, _ = self.check({"CURSOR_AGENT": "1"})
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("cursor", r.stderr)

    def test_stale_snapshot_exits_3(self):
        self.snapshot("copilot", payload("copilot", self.work), age=statusline.STALE_AFTER + 60)
        r, out = self.check({"COPILOT_AGENT_SESSION_ID": SESSION})
        self.assertEqual(r.returncode, 3)
        self.assertIn("status line hasn't refreshed", r.stderr)
        self.assertEqual(out["runtime"], "copilot")

    def test_fresh_enough_snapshot_reports_its_age(self):
        self.snapshot("copilot", payload("copilot", self.work), age=statusline.STALE_AFTER - 60)
        r, out = self.check({"COPILOT_AGENT_SESSION_ID": SESSION})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(out["stale_seconds"], statusline.STALE_AFTER - 61)

    def test_percentage_only_payload(self):
        body = {"session_id": SESSION, "cwd": str(self.work),
                "context_window": {"context_window_size": 400_000, "used_percentage": 50}}
        self.snapshot("copilot", json.dumps(body))
        r, out = self.check({"COPILOT_AGENT_SESSION_ID": SESSION}, ["--role", "worker"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out["used_tokens"], out["context_window"]), (200_000, 400_000))
        self.assertEqual(out["threshold_percentage"], 75)

    def test_no_window_needs_an_explicit_one(self):
        body = {"session_id": SESSION, "context_window": {"current_usage": {"input_tokens": 5}}}
        self.snapshot("copilot", json.dumps(body))
        env = {"COPILOT_AGENT_SESSION_ID": SESSION}
        r, _ = self.check(env)
        self.assertEqual(r.returncode, 3)
        self.assertIn("--window", r.stderr)
        r, out = self.check(env, ["--window", "100"])
        self.assertEqual((r.returncode, out["used_tokens"]), (0, 5))

    def test_no_usage_yet_exits_3(self):
        self.snapshot("copilot", json.dumps({"session_id": SESSION, "context_window": {}}))
        r, _ = self.check({"COPILOT_AGENT_SESSION_ID": SESSION})
        self.assertEqual(r.returncode, 3)
        self.assertIn("no token usage", r.stderr)

    def test_gate_exceeded(self):
        self.snapshot("antigravity", payload("antigravity", self.work))
        r, out = self.check({"ANTIGRAVITY_CONVERSATION_ID": SESSION}, ["--gate", "10"])
        self.assertEqual(r.returncode, 10)
        self.assertTrue(out["over_threshold"])

    def test_transcript_flag_sniffs_the_sidecar_runtime(self):
        path = self.snapshot("antigravity", payload("antigravity", self.work))
        r, out = self.check({}, ["--transcript", str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((out["runtime"], out["measure_source"]), ("antigravity", "statusline"))

    def test_text_output_mentions_the_status_line(self):
        self.snapshot("copilot", payload("copilot", self.work))
        r = helpers.run_ctxcheck([], dict(self.env, COPILOT_AGENT_SESSION_ID=SESSION),
                                 cwd=self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("from the status line", r.stdout)


class DetectionOrderTest(_Tmp):
    def test_order_after_pi(self):
        self.assertEqual(runtime.names()[-3:], ("copilot", "antigravity", "cursor"))

    def test_copilot_beats_antigravity_and_cursor(self):
        for name in ("copilot", "antigravity", "cursor"):
            self.snapshot(name, payload(name, self.work))
        env = dict(self.env, COPILOT_AGENT_SESSION_ID=SESSION,
                   ANTIGRAVITY_CONVERSATION_ID=SESSION, CURSOR_AGENT="1")
        with mock.patch.dict(os.environ, env, clear=True):
            res = runtime.resolve(cwd=self.work)
        self.assertEqual((res.backend.name, res.measure_source), ("copilot", "statusline"))

    def test_unresolved_session_env_is_skipped(self):
        # A leaked COPILOT_AGENT_SESSION_ID with no sidecar anywhere loses to
        # a later runtime whose session resolves.
        self.snapshot("antigravity", payload("antigravity", self.work))
        env = dict(self.env, COPILOT_AGENT_SESSION_ID="leaked",
                   ANTIGRAVITY_CONVERSATION_ID=SESSION)
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(runtime.resolve(cwd=self.work).backend.name, "antigravity")

    def test_cursor_flag_alone_is_weak(self):
        # CURSOR_AGENT=1 without a sidecar for the cwd resolves nothing.
        env = dict(self.env, CURSOR_AGENT="1")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(Unmeasurable) as cm:
                runtime.resolve(cwd=self.work)
        self.assertTrue(any(t.startswith("cursor:") for t in cm.exception.tried))


if __name__ == "__main__":
    unittest.main()
