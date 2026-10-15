"""install-hook cursor | copilot | antigravity against a temp HOME and
COPILOT_HOME. The real ~/.cursor, ~/.copilot and ~/.gemini are never touched:
every run uses clean_env().

Each runtime gets the same contract: a fresh install creates the documented
shape, an install merges into a file holding foreign hooks, re-installing is a
no-op, uninstalling restores the original bytes, and --dry-run writes nothing."""

from __future__ import annotations

import json
import shlex
import tempfile
import unittest
from pathlib import Path

import helpers

CTX = shlex.quote(str(helpers.CTXCHECK))


def ours(runtime: str) -> str:
    return f"python3 {CTX} --hook --runtime {runtime}"


# Synthetic foreign hooks, laid out like hand-edited files (2-space JSON,
# trailing newline).
CURSOR_FOREIGN = """{
  "version": 1,
  "hooks": {
    "postToolUse": [
      {
        "command": "./hooks/audit.sh",
        "matcher": "Write"
      }
    ],
    "beforeShellExecution": [
      {
        "command": "/synthetic/bin/guard.sh",
        "timeout": 5
      }
    ]
  }
}
"""

COPILOT_FOREIGN = """{
  "version": 1,
  "hooks": {
    "postToolUse": [
      {
        "type": "command",
        "bash": "/synthetic/bin/log-tool.sh",
        "timeoutSec": 5
      }
    ],
    "sessionStart": [
      {
        "type": "command",
        "bash": "echo started"
      }
    ]
  }
}
"""

ANTIGRAVITY_FOREIGN = """{
  "agent-state": {
    "PreInvocation": [
      {
        "command": "bash '/synthetic/hooks/agent-state.sh' session",
        "timeout": 10,
        "type": "command"
      }
    ]
  },
  "agent-status": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/sh '/synthetic/agent-hooks/status.sh'",
            "timeout": 10
          }
        ]
      }
    ],
    "Stop": [
      {
        "type": "command",
        "command": "/bin/sh '/synthetic/agent-hooks/status.sh'",
        "timeout": 10
      }
    ]
  }
}
"""


class _Base:
    """Shared install / re-install / uninstall / dry-run cases. Subclasses set
    RUNTIME and FOREIGN and say where the file is and what ours looks like."""
    RUNTIME = ""
    FOREIGN = ""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        self.file = self.hooks_file()

    # -- per runtime ------------------------------------------------------
    def hooks_file(self) -> Path:
        raise NotImplementedError

    def fresh(self) -> dict:
        """The whole file after an install into nothing."""
        raise NotImplementedError

    def entries_after_install(self, data: dict) -> tuple[list, list]:
        """(foreign entries kept, our entries) after installing over FOREIGN."""
        raise NotImplementedError

    # -- helpers ----------------------------------------------------------
    def run_install(self, *flags):
        return helpers.run_ctxcheck(["install-hook", self.RUNTIME, *flags], self.env)

    def seed(self, text):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(text, encoding="utf-8")

    def text(self):
        return self.file.read_text(encoding="utf-8")

    def listing(self):
        d = self.file.parent
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def backups(self):
        return sorted(self.file.parent.glob(f"{self.file.name}.ctxcheck-*.bak"))

    # -- cases ------------------------------------------------------------
    def test_fresh_install_creates_the_documented_shape(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.text()), self.fresh())
        self.assertEqual(self.backups(), [])

    def test_install_merges_and_keeps_foreign_entries(self):
        self.seed(self.FOREIGN)
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        kept, mine = self.entries_after_install(json.loads(self.text()))
        self.assertEqual(kept, self.entries_after_install_foreign())
        self.assertEqual(len(mine), 1)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(self.backups()[0].read_text(encoding="utf-8"), self.FOREIGN)

    def entries_after_install_foreign(self) -> list:
        raise NotImplementedError

    def test_reinstall_is_a_noop(self):
        self.seed(self.FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        installed = self.text()
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already installed", r.stdout)
        self.assertEqual(self.text(), installed)
        self.assertEqual(len(self.backups()), 1)

    def test_uninstall_restores_the_original_bytes(self):
        self.seed(self.FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.text(), self.FOREIGN)

    def test_uninstall_when_not_installed_is_a_noop(self):
        self.seed(self.FOREIGN)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not installed", r.stdout)
        self.assertEqual(self.text(), self.FOREIGN)
        self.assertEqual(self.backups(), [])

    def test_uninstall_with_no_file_is_a_noop(self):
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.file.exists())

    def test_dry_run_writes_nothing(self):
        self.seed(self.FOREIGN)
        before = self.listing()
        r = self.run_install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.text(), self.FOREIGN)
        self.assertEqual(self.listing(), before)
        self.assertIn("+++ ", r.stdout)
        self.assertIn(f"--hook --runtime {self.RUNTIME}", r.stdout)
        self.assertIn("dry run", r.stdout)

    def test_dry_run_without_a_file_creates_nothing(self):
        r = self.run_install("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.file.exists())

    def test_dry_run_uninstall_writes_nothing(self):
        self.seed(self.FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        installed, before = self.text(), self.listing()
        r = self.run_install("--uninstall", "--dry-run")
        # --dry-run and --uninstall are mutually exclusive flags.
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.text(), installed)
        self.assertEqual(self.listing(), before)

    def test_stale_ctxcheck_path_is_updated_not_duplicated(self):
        self.assertEqual(self.run_install().returncode, 0)
        good = self.text()
        self.file.write_text(good.replace(str(helpers.CTXCHECK), "/old/place/scripts/ctxcheck"),
                             encoding="utf-8")
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("updated", r.stdout)
        self.assertEqual(self.text(), good)

    def test_file_with_comments_is_refused(self):
        text = '{\n  // a comment\n}\n'
        self.seed(text)
        r = self.run_install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("strict JSON", r.stderr)
        self.assertEqual(self.text(), text)
        self.assertEqual(self.backups(), [])


class CursorInstallTest(_Base, unittest.TestCase):
    RUNTIME = "cursor"
    FOREIGN = CURSOR_FOREIGN
    ENTRY = {"command": ours("cursor"), "matcher": "Shell", "timeout": 10}

    def hooks_file(self):
        return Path(self.env["HOME"]) / ".cursor" / "hooks.json"

    def fresh(self):
        return {"version": 1, "hooks": {"postToolUse": [self.ENTRY]}}

    def entries_after_install(self, data):
        ptu = data["hooks"]["postToolUse"]
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["hooks"]["beforeShellExecution"],
                         json.loads(CURSOR_FOREIGN)["hooks"]["beforeShellExecution"])
        return ([e for e in ptu if e != self.ENTRY], [e for e in ptu if e == self.ENTRY])

    def entries_after_install_foreign(self):
        return json.loads(CURSOR_FOREIGN)["hooks"]["postToolUse"]

    def test_uninstall_of_a_fresh_install_leaves_the_version(self):
        self.assertEqual(self.run_install().returncode, 0)
        self.assertEqual(self.run_install("--uninstall").returncode, 0)
        self.assertEqual(self.text(), '{\n  "version": 1\n}\n')

    def test_output_names_the_reload(self):
        r = self.run_install()
        self.assertIn("reloads hooks.json", r.stdout)


class CopilotInstallTest(_Base, unittest.TestCase):
    RUNTIME = "copilot"
    FOREIGN = COPILOT_FOREIGN
    ENTRY = {"type": "command", "bash": ours("copilot"), "timeoutSec": 10}

    def setUp(self):
        super().setUp()
        # A sibling hooks file that isn't ours must never be read or written.
        self.other = self.file.parent / "team.json"
        self.other.parent.mkdir(parents=True, exist_ok=True)
        self.other.write_text(COPILOT_FOREIGN, encoding="utf-8")
        self.addCleanup(lambda: self.assertEqual(
            self.other.read_text(encoding="utf-8"), COPILOT_FOREIGN))

    def hooks_file(self):
        # COPILOT_HOME replaces ~/.copilot wholesale; point it outside HOME.
        self.env["COPILOT_HOME"] = str(self.tmp / "copilot-home")
        return self.tmp / "copilot-home" / "hooks" / "ctxcheck.json"

    def fresh(self):
        return {"version": 1, "hooks": {"postToolUse": [self.ENTRY]}}

    def entries_after_install(self, data):
        ptu = data["hooks"]["postToolUse"]
        self.assertEqual(data["hooks"]["sessionStart"],
                         json.loads(COPILOT_FOREIGN)["hooks"]["sessionStart"])
        return ([e for e in ptu if e != self.ENTRY], [e for e in ptu if e == self.ENTRY])

    def entries_after_install_foreign(self):
        return json.loads(COPILOT_FOREIGN)["hooks"]["postToolUse"]

    def test_uninstall_of_a_fresh_install_deletes_our_file(self):
        self.assertEqual(self.run_install().returncode, 0)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.file.exists())
        self.assertIn("deleted the file", r.stdout)
        self.assertEqual(sorted(p.name for p in self.file.parent.glob("*.json")),
                         ["team.json"])

    def test_emptied_file_is_deleted_only_when_owned(self):
        self.assertEqual(self.run_install().returncode, 0)
        installed = self.text()
        from ctxlib import install
        new, what = install.plan_hooks_json("copilot", installed, self.file,
                                            install.COPILOT_HOOKS, True, owned=True)
        self.assertIsNone(new)
        self.assertIn("deleted", what)
        new, _ = install.plan_hooks_json("copilot", installed, self.file,
                                         install.COPILOT_HOOKS, True, owned=False)
        self.assertEqual(new, '{\n  "version": 1\n}\n')

    def test_home_without_copilot_home_uses_dot_copilot(self):
        env = dict(self.env)
        del env["COPILOT_HOME"]
        r = helpers.run_ctxcheck(["install-hook", "copilot"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((Path(env["HOME"]) / ".copilot" / "hooks" / "ctxcheck.json").is_file())


class AntigravityInstallTest(_Base, unittest.TestCase):
    RUNTIME = "antigravity"
    FOREIGN = ANTIGRAVITY_FOREIGN
    MINE = {"PreInvocation": [{"type": "command",
                               "command": ours("antigravity") + " --event PreInvocation",
                               "timeout": 10}]}

    def hooks_file(self):
        return Path(self.env["HOME"]) / ".gemini" / "config" / "hooks.json"

    def fresh(self):
        return {"ctxcheck": self.MINE}

    def entries_after_install(self, data):
        self.assertEqual(list(data)[-1], "ctxcheck")  # appended last
        return ([v for k, v in data.items() if k != "ctxcheck"],
                [data["ctxcheck"]] if data.get("ctxcheck") == self.MINE else [])

    def entries_after_install_foreign(self):
        return list(json.loads(ANTIGRAVITY_FOREIGN).values())

    def test_foreign_name_collision_is_refused(self):
        text = '{\n  "ctxcheck": {\n    "Stop": []\n  }\n}\n'
        self.seed(text)
        for flags in ((), ("--uninstall",)):
            with self.subTest(flags=flags):
                r = self.run_install(*flags)
                self.assertEqual(r.returncode, 1)
                self.assertIn("isn't the one ctxcheck installs", r.stderr)
                self.assertEqual(self.text(), text)

    def test_user_disabled_hook_is_still_recognized(self):
        self.seed(self.FOREIGN)
        self.assertEqual(self.run_install().returncode, 0)
        data = json.loads(self.text())
        data["ctxcheck"] = dict(enabled=False, **data["ctxcheck"])
        self.file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        r = self.run_install()
        self.assertIn("already installed", r.stdout)
        r = self.run_install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ctxcheck", json.loads(self.text()))

    def test_installed_command_warns_only_before_model_calls(self):
        self.assertEqual(self.run_install().returncode, 0)
        cmd = json.loads(self.text())["ctxcheck"]["PreInvocation"][0]["command"]
        self.assertTrue(cmd.endswith("--event PreInvocation"), cmd)
        self.assertNotIn("PostToolUse", json.loads(self.text())["ctxcheck"])


if __name__ == "__main__":
    unittest.main()
