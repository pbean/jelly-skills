"""install-hook codex against a temp CODEX_HOME. The real ~/.codex/hooks.json is
never touched: every run uses clean_env()."""

from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path

import helpers
from ctxlib import jsonedit

OURS = f"python3 {shlex.quote(str(helpers.CTXCHECK))} --hook --runtime codex"
GROUP = {"matcher": "Bash", "hooks": [{"type": "command", "command": OURS, "timeout": 10}]}

# Shaped like a real Codex hooks.json carrying a foreign (Orca-like) hook on
# every event: 2-space JSON, trailing newline, synthetic commands only.
FOREIGN = """{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ": synthetic-agent-hook=1; /synthetic/bin/agent-hook.sh pre",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ": synthetic-agent-hook=1; /synthetic/bin/agent-hook.sh post",
            "timeout": 10
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/synthetic/bin/agent-hook.sh session",
            "timeout": 10
          }
        ]
      },
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash /synthetic/state.sh session",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/synthetic/bin/agent-hook.sh stop",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
"""


class _Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        self.codex = Path(self.env["CODEX_HOME"])
        self.file = self.codex / "hooks.json"

    def run_install(self, *flags, runtime="codex"):
        return helpers.run_ctxcheck(["install-hook", runtime, *flags], self.env)

    def seed(self, text):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(text, encoding="utf-8")

    def text(self):
        return self.file.read_text(encoding="utf-8")

    def backups(self):
        return sorted(self.codex.glob("hooks.json.ctxcheck-*.bak"))

    def post_tool_use(self):
        return json.loads(self.text())["hooks"]["PostToolUse"]


class InstallTest(_Tmp):
    def test_appends_our_group_last_and_keeps_the_rest(self):
        self.seed(FOREIGN)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        groups = self.post_tool_use()
        self.assertEqual(len(groups), 2)
        self.assertNotIn("matcher", groups[0])  # foreign group stays index 0
        self.assertIn("agent-hook.sh post", groups[0]["hooks"][0]["command"])
        self.assertEqual(groups[1], GROUP)
        # Every other event is untouched.
        before, after = json.loads(FOREIGN)["hooks"], json.loads(self.text())["hooks"]
        for event in ("PreToolUse", "SessionStart", "Stop"):
            self.assertEqual(after[event], before[event])
        # Bytes: everything up to the end of the foreign group and everything
        # after the PostToolUse array are unchanged.
        cut = FOREIGN.index('    ],\n    "SessionStart"')
        head = FOREIGN[:cut].rstrip()
        self.assertTrue(self.text().startswith(head), self.text())
        self.assertTrue(self.text().endswith(FOREIGN[cut:]))

    def test_backup_holds_the_original_bytes(self):
        self.seed(FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), FOREIGN)

    def test_output_mentions_trust(self):
        self.seed(FOREIGN)
        r = self.run_install()
        self.assertIn("/hooks", r.stdout)
        self.assertIn("trust", r.stdout)

    def test_reinstall_is_a_noop(self):
        self.seed(FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        installed = self.text()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already installed", r.stdout)
        self.assertEqual(self.text(), installed)
        self.assertEqual(len(self.backups()), 1)

    def test_fresh_install_creates_the_file(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.text()), {"hooks": {"PostToolUse": [GROUP]}})
        self.assertEqual(self.backups(), [])

    def test_install_without_post_tool_use(self):
        text = '{\n  "hooks": {\n    "Stop": []\n  }\n}\n'
        self.seed(text)
        self.assertEqual(self.run_install().returncode, 0)
        data = json.loads(self.text())
        self.assertEqual(data["hooks"]["Stop"], [])
        self.assertEqual(data["hooks"]["PostToolUse"], [GROUP])

    def test_install_without_hooks_key(self):
        self.seed('{\n  "version": 1\n}\n')
        self.assertEqual(self.run_install().returncode, 0)
        data = json.loads(self.text())
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["hooks"], {"PostToolUse": [GROUP]})

    def test_stale_ctxcheck_path_is_updated_not_duplicated(self):
        stale = dict(GROUP, hooks=[dict(GROUP["hooks"][0],
                     command="python3 /old/place/scripts/ctxcheck --hook --runtime codex")])
        self.seed(json.dumps({"hooks": {"PostToolUse": [stale]}}, indent=2) + "\n")
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("updated", r.stdout)
        self.assertEqual(self.post_tool_use(), [GROUP])

    def test_file_with_comments_is_refused(self):
        text = '{\n  // a comment\n  "hooks": {}\n}\n'
        self.seed(text)
        r = self.run_install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("strict JSON", r.stderr)
        self.assertEqual(self.text(), text)
        self.assertEqual(self.backups(), [])


class UninstallTest(_Tmp):
    def test_uninstall_restores_the_original_bytes(self):
        self.seed(FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.text(), FOREIGN)

    def test_uninstall_when_not_installed_is_a_noop(self):
        self.seed(FOREIGN)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not installed", r.stdout)
        self.assertEqual(self.text(), FOREIGN)
        self.assertEqual(self.backups(), [])

    def test_uninstall_with_no_file_is_a_noop(self):
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.file.exists())


class DryRunTest(_Tmp):
    def test_dry_run_writes_nothing(self):
        self.seed(FOREIGN)
        listing = sorted(p.name for p in self.codex.iterdir())
        r = self.run_install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.text(), FOREIGN)
        self.assertEqual(sorted(p.name for p in self.codex.iterdir()), listing)
        self.assertIn("+++ ", r.stdout)
        self.assertIn("--hook --runtime codex", r.stdout)
        self.assertIn("dry run", r.stdout)
        self.assertIn("/hooks", r.stdout)

    def test_dry_run_without_a_file_creates_nothing(self):
        r = self.run_install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.file.exists())


class OtherRuntimesTest(_Tmp):
    def test_claude_needs_no_installer(self):
        r = self.run_install(runtime="claude")
        self.assertEqual(r.returncode, 1)
        self.assertIn("context-budget skill", r.stderr)

    def test_opencode_and_pi_install_plugin_files_not_hooks_json(self):
        for runtime in ("opencode", "pi"):
            with self.subTest(runtime=runtime):
                r = self.run_install("--dry-run", runtime=runtime)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("ctxcheck-context-budget.ts", r.stdout)
                self.assertFalse(self.file.exists())


class ArrayEditTest(unittest.TestCase):
    def _round_trip(self, text, value):
        arr = jsonedit.parse(text).get("a").value
        added = jsonedit.append_item(text, arr, value)
        arr2 = jsonedit.parse(added).get("a").value
        self.assertEqual(json.loads(added)["a"][-1], value)
        self.assertEqual(jsonedit.remove_item(added, arr2, len(arr2.items) - 1), text)
        return added

    def test_multi_line_array(self):
        text = '{\n  "a": [\n    1,\n    {\n      "b": 2\n    }\n  ]\n}\n'
        added = self._round_trip(text, {"c": [3]})
        self.assertIn('\n    {\n      "c": [\n        3\n      ]\n    }\n  ]', added)

    def test_one_line_array(self):
        added = self._round_trip('{"a": [1, 2]}', {"c": 3})
        self.assertEqual(added, '{"a": [1, 2, {"c": 3}]}')

    def test_empty_array(self):
        self._round_trip('{"a": []}', 1)
        self._round_trip('{\n  "a": []\n}\n', {"c": 3})

    def test_remove_first_and_middle(self):
        text = '{"a": [1, 2, 3]}'
        arr = jsonedit.parse(text).get("a").value
        self.assertEqual(jsonedit.remove_item(text, arr, 0), '{"a": [2, 3]}')
        self.assertEqual(jsonedit.remove_item(text, arr, 1), '{"a": [1, 3]}')

    def test_items_are_recorded(self):
        text = '{"a": [1, "x", [2], {"k": null}]}'
        arr = jsonedit.parse(text).get("a").value
        self.assertEqual([text[n.start:n.end] for n in arr.items],
                         ["1", '"x"', "[2]", '{"k": null}'])


if __name__ == "__main__":
    unittest.main()
