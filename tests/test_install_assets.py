"""install-hook opencode | pi: a plugin / extension file copied into a temp
HOME's plugin directory. The real ~/.config/opencode and ~/.pi/agent are never
touched: every run uses clean_env()."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers
from ctxlib import install

FOREIGN = "// synthetic foreign extension\nexport default function (pi) {}\n"


class _Assets(unittest.TestCase):
    RUNTIME = ""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.env = helpers.clean_env(self.tmp)
        self.home = Path(self.env["HOME"])

    @property
    def dir(self) -> Path:
        raise NotImplementedError

    @property
    def file(self) -> Path:
        return self.dir / "ctxcheck-context-budget.ts"

    def run_install(self, *args: str, runtime: str | None = None, env: dict | None = None):
        return helpers.run_ctxcheck(["install-hook", runtime or self.RUNTIME, *args],
                                    env or self.env)

    def ok(self, *args: str, env: dict | None = None):
        r = self.run_install(*args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def foreign(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        other = self.dir / "orca-synthetic.ts"
        other.write_text(FOREIGN, encoding="utf-8")
        return other

    @staticmethod
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def listing(self) -> list:
        return sorted(str(p.relative_to(self.tmp)) for p in self.tmp.rglob("*"))


class _AssetCases:
    """Shared by both runtimes (mixed into a _Assets subclass)."""

    def test_install_fills_in_the_absolute_ctxcheck_path(self):
        r = self.ok()
        self.assertIn("added the ctxcheck", r.stdout)
        text = self.file.read_text(encoding="utf-8")
        self.assertNotIn(install.PLACEHOLDER, text)
        self.assertIn(json.dumps(str(helpers.CTXCHECK)), text)
        self.assertTrue(Path(str(helpers.CTXCHECK)).is_absolute())
        self.assertIn(install.OWNED_MARK, text.splitlines()[0])
        self.assertEqual(oct(self.file.stat().st_mode & 0o777), oct(0o644))

    def test_reinstall_is_a_no_op(self):
        self.ok()
        before, mtime = self.sha(self.file), self.file.stat().st_mtime_ns
        r = self.ok()
        self.assertIn("already installed; nothing to change", r.stdout)
        self.assertEqual(self.sha(self.file), before)
        self.assertEqual(self.file.stat().st_mtime_ns, mtime)
        self.assertEqual([p.name for p in self.dir.iterdir()], [self.file.name])

    def test_stale_copy_is_updated_without_a_backup(self):
        self.ok()
        stale = self.file.read_text().replace(json.dumps(str(helpers.CTXCHECK)),
                                              '"/old/path/ctxcheck"')
        self.file.write_text(stale)
        r = self.ok()
        self.assertIn("updated the ctxcheck", r.stdout)
        self.assertEqual(self.file.read_text(), install.render_asset(self.ASSET))
        self.assertEqual([p.name for p in self.dir.iterdir()], [self.file.name])

    def test_uninstall_removes_only_our_file(self):
        other = self.foreign()
        before = self.sha(other)
        self.ok()
        self.assertTrue(self.file.is_file())
        self.assertEqual(self.sha(other), before)
        r = self.ok("--uninstall")
        self.assertIn("removed the ctxcheck", r.stdout)
        self.assertFalse(self.file.exists())
        self.assertEqual(self.sha(other), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], [other.name])
        r = self.ok("--uninstall")
        self.assertIn("not installed; nothing to change", r.stdout)

    def test_dry_run_writes_nothing(self):
        other = self.foreign()
        before_listing, before = self.listing(), self.sha(other)
        r = self.ok("--dry-run")
        self.assertIn("(dry run:", r.stdout)
        self.assertIn("+++ " + str(self.file), r.stdout)
        self.assertEqual(self.listing(), before_listing)
        self.assertEqual(self.sha(other), before)

    def test_dry_run_on_an_empty_home_creates_no_directory(self):
        before = self.listing()
        self.ok("--dry-run")
        self.assertEqual(self.listing(), before)

    def test_a_foreign_file_with_our_name_is_refused(self):
        self.dir.mkdir(parents=True)
        self.file.write_text(FOREIGN)
        for args in ((), ("--uninstall",), ("--dry-run",)):
            with self.subTest(args=args):
                r = self.run_install(*args)
                self.assertEqual(r.returncode, 1)
                self.assertIn("not written by ctxcheck", r.stderr)
                self.assertEqual(self.file.read_text(), FOREIGN)

    def test_asset_keeps_the_placeholder_in_the_repo(self):
        src = (install.ASSETS / self.ASSET).read_text(encoding="utf-8")
        self.assertEqual(src.count(f'"{install.PLACEHOLDER}"'), 1)
        self.assertIn(install.OWNED_MARK, src.splitlines()[0])
        self.assertNotIn("import ", "\n".join(
            line for line in src.splitlines() if line.startswith("import ")
            and "node:" not in line))


class OpenCodeAssetTest(_AssetCases, _Assets):
    RUNTIME = "opencode"
    ASSET = "opencode-plugin.ts"

    @property
    def dir(self) -> Path:
        return Path(self.env["XDG_CONFIG_HOME"]) / "opencode" / install.OPENCODE_PLUGIN_DIRNAME

    def test_without_xdg_config_home_it_uses_dot_config(self):
        env = {k: v for k, v in self.env.items() if k != "XDG_CONFIG_HOME"}
        r = self.ok(env=env)
        self.assertTrue((self.home / ".config" / "opencode" / install.OPENCODE_PLUGIN_DIRNAME
                         / self.file.name).is_file(), r.stdout)


class PiAssetTest(_AssetCases, _Assets):
    RUNTIME = "pi"
    ASSET = "pi-extension.ts"

    @property
    def dir(self) -> Path:
        return self.home / ".pi" / "agent" / "extensions"

    def test_pi_coding_agent_dir_is_honored(self):
        agent = self.tmp / "custom-agent"
        env = dict(self.env, PI_CODING_AGENT_DIR=str(agent))
        self.ok(env=env)
        self.assertTrue((agent / "extensions" / self.file.name).is_file())
        self.assertFalse(self.dir.exists())


class RenderTest(unittest.TestCase):
    def test_path_is_a_valid_js_string_literal(self):
        old = install.CTXCHECK
        try:
            install.CTXCHECK = Path('/odd dir/with "quote" and \\ back/ctxcheck')
            text = install.render_asset("pi-extension.ts")
        finally:
            install.CTXCHECK = old
        self.assertIn(json.dumps('/odd dir/with "quote" and \\ back/ctxcheck'), text)
        self.assertNotIn(install.PLACEHOLDER, text)


if __name__ == "__main__":
    unittest.main()
