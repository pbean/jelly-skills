from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import helpers

JSON_KEYS = {
    "runtime", "role", "used_tokens", "context_window", "window_source",
    "used_percentage", "remaining_percentage", "remaining_tokens", "model",
    "session_id", "transcript", "measure_source", "stale_seconds",
    "threshold_percentage", "threshold_source", "over_threshold",
}


class _Claude(unittest.TestCase):
    """A clean env with a synthetic Claude session (43,000 used, opus 5.5)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bare_env = helpers.clean_env(self.tmp)
        self.env = dict(self.bare_env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION)
        helpers.install_claude_transcript(self.env["HOME"])

    def run_json(self, *args: str) -> dict:
        r = helpers.run_ctxcheck(["--json", *args], self.env)
        self.assertIn(r.returncode, (0, 10), r.stderr)
        return json.loads(r.stdout)


class JsonShapeTest(_Claude):
    def test_json_keys(self):
        out = self.run_json()
        self.assertEqual(set(out), JSON_KEYS)
        self.assertEqual(out["runtime"], "claude")
        self.assertEqual(out["used_tokens"], 43_000)
        self.assertEqual(out["context_window"], 1_000_000)
        self.assertEqual(out["measure_source"], "transcript")
        self.assertIsNone(out["stale_seconds"])
        self.assertEqual(out["session_id"], helpers.CLAUDE_SESSION)

    def test_no_role_no_gate_reports_without_threshold(self):
        out = self.run_json()
        self.assertIsNone(out["role"])
        self.assertIsNone(out["threshold_percentage"])
        self.assertIsNone(out["over_threshold"])

    def test_role_derives_threshold(self):
        out = self.run_json("--role", "coordinator")
        self.assertEqual(out["role"], "coordinator")
        self.assertEqual(out["threshold_percentage"], 26.0)
        self.assertFalse(out["over_threshold"])


class GateTest(_Claude):
    def test_exit_10_when_over_gate(self):
        r = helpers.run_ctxcheck(["--gate", "0.0001"], self.env)
        self.assertEqual(r.returncode, 10, r.stderr)
        self.assertIn("EXCEEDED", r.stdout)

    def test_exit_0_under_gate(self):
        r = helpers.run_ctxcheck(["--gate", "50"], self.env)
        self.assertEqual(r.returncode, 0, r.stderr)


class WindowOverrideTest(_Claude):
    def test_window_flag_wins(self):
        self.assertEqual(self.run_json("--window", "100000")["context_window"], 100_000)

    def test_ctxcheck_window_env(self):
        self.env["CTXCHECK_WINDOW"] = "400000"
        out = self.run_json()
        self.assertEqual((out["context_window"], out["window_source"]),
                         (400_000, "CTXCHECK_WINDOW"))

    def test_preset_flag(self):
        self.assertEqual(self.run_json("--preset", "claude-200k")["context_window"], 200_000)


class UnmeasurableTest(_Claude):
    def test_exit_3_with_no_runtime(self):
        r = helpers.run_ctxcheck(["--json"], self.bare_env)
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(json.loads(r.stdout)["measurable"])


class ListPresetsTest(unittest.TestCase):
    def test_lists_presets_and_role_bands(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = helpers.run_ctxcheck(["--list-presets"], helpers.clean_env(tmp))
        self.assertEqual(r.returncode, 0, r.stderr)
        for word in ("claude-1m", "claude-200k", "codex-272k-effective",
                     "worker", "planner", "coordinator", "26.0%", "28.0%", "75.0%", "80.0%"):
            self.assertIn(word, r.stdout)


class NotImplementedTest(unittest.TestCase):
    CASES = (
        ["install-hook", "cursor"],
        ["launch-command", "--json"],
    )

    def test_reserved_verbs_parse_then_exit_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = helpers.clean_env(tmp)
            for args in self.CASES:
                with self.subTest(args=args):
                    r = helpers.run_ctxcheck(args, env)
                    self.assertEqual(r.returncode, 1)
                    self.assertIn("not implemented in this version", r.stderr)

    def test_usage_error_exits_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = helpers.run_ctxcheck(["--role", "reviewer"], helpers.clean_env(tmp))
        self.assertEqual(r.returncode, 1)


if __name__ == "__main__":
    unittest.main()
