"""--arm / --disarm / stale-marker reaping, and a plain ctxcheck reading the
marker. Every run uses clean_env(): CTXCHECK_HOME is a temp dir."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import helpers

SCHEMA = {"sessionId", "runtime", "role", "label", "contextWindow",
          "handoffPct", "warnedAtPct", "armedAt"}
LABEL = "session 4 of /abs/plan.md"


class _Armed(unittest.TestCase):
    """A clean env with a synthetic Claude session (43,000 used, 1M window)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bare_env = helpers.clean_env(self.tmp)
        self.env = dict(self.bare_env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION)
        self.transcript = helpers.install_claude_transcript(self.env["HOME"])
        self.markers = Path(self.env["CTXCHECK_HOME"]) / "markers"

    def run_ok(self, *args: str, env: dict | None = None):
        r = helpers.run_ctxcheck(list(args), env or self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def arm(self, *extra: str, env: dict | None = None):
        return self.run_ok("--arm", "--label", LABEL, *extra, env=env)

    def marker(self, session: str = helpers.CLAUDE_SESSION) -> dict:
        return json.loads((self.markers / f"{session}.json").read_text())


class ArmTest(_Armed):
    def test_arm_writes_the_schema(self):
        r = self.arm("--role", "worker")
        self.assertIn("armed:", r.stdout)
        m = self.marker()
        self.assertEqual(set(m), SCHEMA)
        self.assertEqual(m["sessionId"], helpers.CLAUDE_SESSION)
        self.assertEqual(m["runtime"], "claude")
        self.assertEqual(m["role"], "worker")
        self.assertEqual(m["label"], LABEL)
        self.assertEqual(m["contextWindow"], 1_000_000)
        self.assertEqual(m["handoffPct"], 28.0)
        self.assertIsNone(m["warnedAtPct"])
        time.strptime(m["armedAt"], "%Y-%m-%dT%H:%M:%SZ")

    def test_derived_gate_per_role_on_1m(self):
        for role, gate in (("worker", 28.0), ("planner", 28.0), ("coordinator", 26.0)):
            with self.subTest(role=role):
                self.arm("--role", role)
                self.assertEqual(self.marker()["role"], role)
                self.assertEqual(self.marker()["handoffPct"], gate)

    def test_derived_gate_per_role_on_codex_window(self):
        env = dict(self.bare_env, CODEX_THREAD_ID=helpers.CODEX_THREAD)
        helpers.install_codex_rollout(env["CODEX_HOME"])
        for role in ("worker", "coordinator"):
            with self.subTest(role=role):
                self.arm("--role", role, env=env)
                m = self.marker(helpers.CODEX_THREAD)
                self.assertEqual(m["runtime"], "codex")
                self.assertEqual(m["contextWindow"], 258_400)
                self.assertEqual(m["handoffPct"], 75.0)

    def test_explicit_gate_overrides_band(self):
        self.arm("--role", "coordinator", "--gate", "40")
        self.assertEqual(self.marker()["handoffPct"], 40.0)

    def test_window_overrides_discovery(self):
        self.arm("--role", "worker", "--window", "200000")
        m = self.marker()
        self.assertEqual(m["contextWindow"], 200_000)
        self.assertEqual(m["handoffPct"], 75.0)  # band follows the window

    def test_arm_without_role_exits_1(self):
        r = helpers.run_ctxcheck(["--arm", "--label", LABEL], self.env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--role", r.stderr)
        self.assertFalse(self.markers.exists() and any(self.markers.iterdir()))

    def test_arm_with_no_runtime_exits_3(self):
        r = helpers.run_ctxcheck(["--arm", "--role", "worker", "--label", LABEL],
                                 self.bare_env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(self.markers.exists() and any(self.markers.iterdir()))


class MarkerDefaultsTest(_Armed):
    def test_plain_ctxcheck_uses_marker(self):
        self.arm("--role", "coordinator", "--window", "700000")
        out = json.loads(self.run_ok("--json").stdout)
        self.assertEqual(out["role"], "coordinator")
        self.assertEqual(out["context_window"], 700_000)
        self.assertEqual(out["window_source"], "marker")
        self.assertEqual(out["threshold_percentage"], 26.0)
        self.assertEqual(out["threshold_source"], "marker")
        self.assertFalse(out["over_threshold"])

    def test_plain_ctxcheck_exits_10_over_marker_gate(self):
        self.arm("--role", "worker", "--gate", "1")
        r = helpers.run_ctxcheck(["--json"], self.env)
        self.assertEqual(r.returncode, 10, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["threshold_percentage"], 1.0)
        self.assertTrue(out["over_threshold"])

    def test_command_line_gate_beats_marker(self):
        self.arm("--role", "worker", "--gate", "1")
        r = helpers.run_ctxcheck(["--json", "--gate", "50"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["threshold_percentage"], 50.0)
        self.assertEqual(out["threshold_source"], "--gate")

    def test_unarmed_session_has_no_threshold(self):
        out = json.loads(self.run_ok("--json").stdout)
        self.assertIsNone(out["role"])
        self.assertIsNone(out["threshold_percentage"])


class DisarmTest(_Armed):
    def test_disarm_removes_marker_then_is_a_no_op(self):
        self.arm("--role", "worker")
        path = self.markers / f"{helpers.CLAUDE_SESSION}.json"
        self.assertTrue(path.is_file())
        r = self.run_ok("--disarm")
        self.assertIn("disarmed", r.stdout)
        self.assertFalse(path.exists())
        r = self.run_ok("--disarm")
        self.assertIn("not armed", r.stdout)

    def test_disarm_falls_back_to_env_session_without_transcript(self):
        self.arm("--role", "worker")
        self.transcript.unlink()
        r = self.run_ok("--disarm")
        self.assertIn("disarmed", r.stdout)
        self.assertFalse((self.markers / f"{helpers.CLAUDE_SESSION}.json").exists())

    def test_disarm_with_no_session_is_a_no_op(self):
        r = self.run_ok("--disarm", env=self.bare_env)
        self.assertIn("not armed", r.stdout)


class ReapTest(_Armed):
    def _stale(self, name: str, days: float) -> Path:
        self.markers.mkdir(parents=True, exist_ok=True)
        p = self.markers / f"{name}.json"
        p.write_text(json.dumps({"sessionId": name}) + "\n")
        t = time.time() - days * 86400
        os.utime(p, (t, t))
        return p

    def test_reaps_only_markers_older_than_7_days(self):
        old = self._stale("old-session", 8)
        recent = self._stale("recent-session", 6)
        r = self.arm("--role", "worker")
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertIn("reaped 1", r.stdout)

    def test_never_reaps_the_session_being_armed(self):
        self.arm("--role", "worker")
        own = self.markers / f"{helpers.CLAUDE_SESSION}.json"
        t = time.time() - 8 * 86400
        os.utime(own, (t, t))
        self.arm("--role", "coordinator")
        self.assertTrue(own.is_file())
        self.assertEqual(self.marker()["role"], "coordinator")



class ReapStaleUnitTest(unittest.TestCase):
    """reap_stale() directly: arming refreshes its own marker's mtime before
    it reaps, so only a direct call proves `keep` is honored."""

    def test_keep_survives_even_when_old(self):
        from unittest import mock
        from ctxlib import markers
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"CTXCHECK_HOME": tmp}):
            d = Path(tmp) / "markers"
            d.mkdir()
            old = time.time() - 8 * 86400
            keep, other = d / "keep.json", d / "other.json"
            for p in (keep, other):
                p.write_text("{}")
                os.utime(p, (old, old))
            self.assertEqual(markers.reap_stale(keep=keep), 1)
            self.assertTrue(keep.exists())
            self.assertFalse(other.exists())


if __name__ == "__main__":
    unittest.main()
