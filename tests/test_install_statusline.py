"""install-statusline against a temp HOME / COPILOT_HOME / CURSOR_CONFIG_DIR.
Real settings files are never touched: every run uses clean_env()."""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib import install

ORIGINAL = "bash ~/.statusline.sh | head -1"
WRAP = f"python3 {shlex.quote(str(helpers.CTXCHECK))} statusline-tee --runtime"

# Shaped like the real files (2-space JSON, trailing newline), plus a nested
# array and a key after the status line, so an edit that re-serializes or
# reorders shows up as a byte difference.
AGY_SETTINGS = '{\n  "enableTelemetry": false,\n  "trustedWorkspaces": [\n    "/synthetic"\n  ]\n}\n'
WITH_STATUSLINE = ('{\n  "model": "synthetic",\n  "statusLine": {\n    "type": "command",\n'
                   f'    "command": {json.dumps(ORIGINAL)},\n    "padding": 1\n  }},\n'
                   '  "permissions": {"allow": ["Shell(ls)"], "deny": []}\n}\n')


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp, CURSOR_CONFIG_DIR=str(self.tmp / "cursor-cfg"))
        self.home = Path(self.env["HOME"])
        self.files = {
            "antigravity": self.home / ".gemini" / "antigravity-cli" / "settings.json",
            "copilot": Path(self.env["COPILOT_HOME"]) / "settings.json",
            "cursor": self.tmp / "cursor-cfg" / "cli-config.json",
        }

    def run_install(self, runtime, *flags):
        return helpers.run_ctxcheck(["install-statusline", runtime, *flags], self.env)

    def seed(self, runtime, text):
        path = self.files[runtime]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def command(self, runtime):
        return json.loads(self.files[runtime].read_text())["statusLine"]["command"]

    def backups(self, runtime):
        path = self.files[runtime]
        return sorted(path.parent.glob(f"{path.name}.ctxcheck-*.bak"))


class FreshInstallTest(_Tmp):
    def test_adds_status_line_keeping_the_rest_byte_for_byte(self):
        path = self.seed("antigravity", AGY_SETTINGS)
        r = self.run_install("antigravity")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = path.read_text()
        self.assertTrue(text.startswith(AGY_SETTINGS[:-3] + ",\n"), text)
        self.assertEqual(json.loads(text)["statusLine"],
                         {"type": "command", "command": f"{WRAP} antigravity"})
        self.assertIn('\n  "statusLine": {\n    "type": "command",', text)
        self.assertEqual(len(self.backups("antigravity")), 1)
        self.assertEqual(self.backups("antigravity")[0].read_text(), AGY_SETTINGS)

    def test_copilot_respects_copilot_home_and_creates_the_file(self):
        r = self.run_install("copilot")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.files["copilot"].is_file())
        self.assertFalse((self.home / ".copilot").exists() and
                         self.env["COPILOT_HOME"] != str(self.home / ".copilot"))
        self.assertEqual(self.command("copilot"), f"{WRAP} copilot")
        self.assertEqual(self.backups("copilot"), [])  # nothing to back up

    def test_cursor_requires_an_existing_config(self):
        r = self.run_install("cursor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)
        self.assertFalse(self.files["cursor"].exists())

    def test_unsupported_runtime_is_a_usage_error(self):
        r = self.run_install("claude")
        self.assertEqual(r.returncode, 1)

    def test_preserves_file_mode(self):
        path = self.seed("antigravity", AGY_SETTINGS)
        os.chmod(path, 0o600)
        self.assertEqual(self.run_install("antigravity").returncode, 0)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class IdempotentTest(_Tmp):
    def test_reinstall_is_a_no_op(self):
        path = self.seed("cursor", WITH_STATUSLINE)
        self.assertEqual(self.run_install("cursor").returncode, 0)
        once = path.read_bytes()
        r = self.run_install("cursor")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already installed", r.stdout)
        self.assertEqual(path.read_bytes(), once)
        self.assertEqual(len(self.backups("cursor")), 1)  # no second backup

    def test_moved_ctxcheck_is_rewrapped_not_double_wrapped(self):
        stale = f"python3 /old/skills/context-budget/scripts/ctxcheck statusline-tee " \
                f"--runtime cursor -- {shlex.quote(ORIGINAL)}"
        self.seed("cursor", WITH_STATUSLINE.replace(json.dumps(ORIGINAL), json.dumps(stale)))
        r = self.run_install("cursor")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.command("cursor"), f"{WRAP} cursor -- {shlex.quote(ORIGINAL)}")

    def test_wrap_for_another_runtime_is_refused(self):
        other = f"{WRAP} copilot -- {shlex.quote(ORIGINAL)}"
        path = self.seed("cursor", WITH_STATUSLINE.replace(json.dumps(ORIGINAL), json.dumps(other)))
        before = path.read_bytes()
        r = self.run_install("cursor")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(path.read_bytes(), before)


class WrapTest(_Tmp):
    def test_wraps_existing_command(self):
        path = self.seed("cursor", WITH_STATUSLINE)
        r = self.run_install("cursor")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("wrapped the existing status line", r.stdout)
        self.assertEqual(self.command("cursor"), f"{WRAP} cursor -- {shlex.quote(ORIGINAL)}")
        # Only the command string changed.
        self.assertEqual(path.read_text().replace(json.dumps(self.command("cursor")), "X"),
                         WITH_STATUSLINE.replace(json.dumps(ORIGINAL), "X"))

    def test_wrapped_command_round_trips_through_parse_wrap(self):
        for original in (ORIGINAL, "it's \"quoted\" $HOME", "/path with space/s.sh"):
            with self.subTest(original=original):
                w = install.parse_wrap(install.wrap_command("copilot", original))
                self.assertEqual((w.runtime, w.original), ("copilot", original))
        self.assertIsNone(install.parse_wrap(ORIGINAL))
        self.assertIsNone(install.parse_wrap("echo statusline-tee"))

    def test_wrapped_command_runs_the_original_through_the_tee(self):
        # End to end: the installed command, run by a shell the way the
        # agent runs it, writes the sidecar and prints the original's output.
        path = self.seed("copilot", '{\n  "statusLine": {"command": "cat | wc -c"}\n}\n')
        self.assertEqual(self.run_install("copilot").returncode, 0)
        cmd = json.loads(path.read_text())["statusLine"]["command"]
        import subprocess
        data = json.dumps({"session_id": "s1", "cwd": "/w",
                           "context_window": {"context_window_size": 10, "used_percentage": 50}})
        r = subprocess.run(["sh", "-c", cmd], input=data, capture_output=True, text=True,
                           env=self.env, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, str(len(data))), r.stderr)
        sidecar = Path(self.env["CTXCHECK_HOME"]) / "statusline" / "copilot" / "s1.json"
        self.assertTrue(sidecar.is_file())


class UninstallTest(_Tmp):
    def test_restores_exact_original_after_wrap(self):
        path = self.seed("cursor", WITH_STATUSLINE)
        self.assertEqual(self.run_install("cursor").returncode, 0)
        r = self.run_install("cursor", "--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(path.read_text(), WITH_STATUSLINE)

    def test_restores_exact_original_after_fresh_install(self):
        for runtime, text in (("antigravity", AGY_SETTINGS), ("copilot", "{}\n"),
                              ("copilot", '{"a": [1, 2], "b": {}}')):
            with self.subTest(runtime=runtime, text=text):
                path = self.seed(runtime, text)
                self.assertEqual(self.run_install(runtime).returncode, 0)
                self.assertNotEqual(path.read_text(), text)
                self.assertEqual(self.run_install(runtime, "--uninstall").returncode, 0)
                self.assertEqual(path.read_text(), text)

    def test_keeps_edits_made_since_install(self):
        path = self.seed("antigravity", AGY_SETTINGS)
        self.assertEqual(self.run_install("antigravity").returncode, 0)
        path.write_text(path.read_text().replace("false", "true"))
        self.assertEqual(self.run_install("antigravity", "--uninstall").returncode, 0)
        self.assertEqual(path.read_text(), AGY_SETTINGS.replace("false", "true"))

    def test_keeps_status_line_keys_the_user_added(self):
        path = self.seed("antigravity", AGY_SETTINGS)
        self.assertEqual(self.run_install("antigravity").returncode, 0)
        data = json.loads(path.read_text())
        data["statusLine"]["padding"] = 2
        path.write_text(json.dumps(data, indent=2) + "\n")
        self.assertEqual(self.run_install("antigravity", "--uninstall").returncode, 0)
        self.assertEqual(json.loads(path.read_text())["statusLine"], {"type": "command",
                                                                      "padding": 2})

    def test_not_installed_is_a_no_op(self):
        path = self.seed("cursor", WITH_STATUSLINE)
        r = self.run_install("cursor", "--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not installed", r.stdout)
        self.assertEqual(path.read_text(), WITH_STATUSLINE)
        self.assertEqual(self.backups("cursor"), [])
        r = self.run_install("copilot", "--uninstall")  # no file at all
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.files["copilot"].exists())


class DryRunTest(_Tmp):
    def test_writes_nothing_and_prints_a_diff(self):
        path = self.seed("antigravity", AGY_SETTINGS)
        before = sorted(p.name for p in path.parent.iterdir())
        r = self.run_install("antigravity", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(path.read_text(), AGY_SETTINGS)
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), before)
        self.assertIn(f"--- {path}", r.stdout)
        self.assertIn('+  "statusLine": {', r.stdout)
        self.assertIn("dry run", r.stdout)

    def test_missing_file_is_not_created(self):
        r = self.run_install("copilot", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--- /dev/null", r.stdout)
        self.assertFalse(self.files["copilot"].exists())
        self.assertFalse(self.files["copilot"].parent.exists())

    def test_dry_run_and_uninstall_are_exclusive(self):
        r = self.run_install("copilot", "--dry-run", "--uninstall")
        self.assertEqual(r.returncode, 1)


class RefusalTest(_Tmp):
    def test_non_strict_json_is_refused_untouched(self):
        text = '{\n  // comment\n  "a": 1,\n}\n'
        path = self.seed("antigravity", text)
        r = self.run_install("antigravity")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not strict JSON", r.stderr)
        self.assertEqual(path.read_text(), text)

    def test_odd_status_line_shapes_are_refused(self):
        for text in ('{"statusLine": "x"}', '{"statusLine": {"command": 5}}', "[]"):
            with self.subTest(text=text):
                path = self.seed("copilot", text)
                self.assertEqual(self.run_install("copilot").returncode, 1)
                self.assertEqual(path.read_text(), text)


class TargetsTest(unittest.TestCase):
    def test_paths_follow_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = helpers.clean_env(tmp)
            with mock.patch.dict(os.environ, env, clear=True):
                t = install.STATUSLINE_TARGETS
                self.assertEqual(t["copilot"].path(), Path(env["COPILOT_HOME"]) / "settings.json")
                self.assertEqual(t["cursor"].path(),
                                 Path(env["HOME"]) / ".cursor" / "cli-config.json")
                self.assertEqual(t["antigravity"].path(), Path(env["HOME"]) / ".gemini" /
                                 "antigravity-cli" / "settings.json")


if __name__ == "__main__":
    unittest.main()
