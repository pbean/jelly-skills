"""launch-command: find the agent among our ancestors and strip resume args.

All argv lists and process tables here are synthetic."""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import helpers  # noqa: F401 - puts the scripts dir on sys.path

from ctxlib import EXIT_OK, EXIT_UNMEASURABLE, launch

SID = "0f0e0d0c-0b0a-4908-8706-050403020100"


def relaunch(argv: list[str]) -> tuple[str, list[str], list[str]]:
    """(runtime, argv to launch, stripped) for one process's argv."""
    spec, prefix, args = launch.identify(argv)
    kept, dropped = launch.strip(spec, args)
    return spec.runtime, prefix + kept, dropped


class StripTest(unittest.TestCase):
    """Resume, continue, fork and the initial prompt go; the posture stays."""

    CASES = (
        # (argv, expected argv)
        (["claude", "--dangerously-skip-permissions", "--model", "opus"],
         ["claude", "--dangerously-skip-permissions", "--model", "opus"]),
        (["claude", "--resume", SID, "--model", "opus", "fix the tests"],
         ["claude", "--model", "opus"]),
        (["claude", "-r", SID, "--fork-session", "--permission-mode", "plan"],
         ["claude", "--permission-mode", "plan"]),
        (["claude", "--resume", "--model=opus"], ["claude", "--model=opus"]),
        (["claude", "--resume=" + SID, "-c", "--session-id", SID, "--effort", "high"],
         ["claude", "--effort", "high"]),
        (["claude", "--continue", "--add-dir", "/a", "/b", "--verbose", "go on"],
         ["claude", "--add-dir", "/a", "/b", "--verbose"]),
        (["claude", "--debug", "--", "a prompt"], ["claude", "--debug"]),
        (["codex", "--no-daemon", "resume", SID, "--model", "gpt-5.5", "carry on"],
         ["codex", "--no-daemon", "--model", "gpt-5.5"]),
        (["codex", "resume", "--last", "-c", "model_reasoning_effort=high"],
         ["codex", "-c", "model_reasoning_effort=high"]),
        (["codex", "fork", SID, "--sandbox", "workspace-write", "-a", "never"],
         ["codex", "--sandbox", "workspace-write", "-a", "never"]),
        (["codex", "-p", "work", "write the docs"], ["codex", "-p", "work"]),
        (["opencode", "--continue", "-m", "anthropic/claude-opus-5-5", "/srv/repo"],
         ["opencode", "-m", "anthropic/claude-opus-5-5", "/srv/repo"]),
        (["opencode", "-s", "ses_123", "--fork", "--prompt", "hello", "--agent", "build"],
         ["opencode", "--agent", "build"]),
        (["pi", "--session", "/tmp/s.jsonl", "--model", "opus", "@notes.md", "go"],
         ["pi", "--model", "opus"]),
        (["pi", "-c", "--ext-flag", "on", "-r", "--fork", "abc"],
         ["pi", "--ext-flag", "on"]),
        (["agent", "--resume", "chat-1", "--model", "gpt-5", "-f", "do it"],
         ["agent", "--model", "gpt-5", "-f"]),
        (["cursor-agent", "resume", "--sandbox", "enabled"],
         ["cursor-agent", "--sandbox", "enabled"]),
        (["copilot", "--resume=abc", "--allow-all-tools", "--model", "gpt-5"],
         ["copilot", "--allow-all-tools", "--model", "gpt-5"]),
        (["copilot", "--continue", "-i", "start here", "--session-id", "x"],
         ["copilot"]),
        (["agy", "-c", "--dangerously-skip-permissions", "-model", "gemini-3"],
         ["agy", "--dangerously-skip-permissions", "-model", "gemini-3"]),
        (["agy", "--conversation", "conv-9", "-p", "hi", "--sandbox", "trailing"],
         ["agy", "--sandbox"]),
        (["agy", "--continue=true", "stray", "--model", "x"], ["agy"]),
    )

    def test_cases(self):
        for argv, want in self.CASES:
            with self.subTest(argv=argv):
                _rt, got, dropped = relaunch(argv)
                self.assertEqual(got, want)
                # Nothing is lost silently: kept + stripped is the original.
                self.assertEqual(sorted(got[1:] + dropped), sorted(argv[1:]))  # prefix is argv[0]

    def test_runtimes(self):
        names = {"claude": "claude", "codex": "codex", "opencode": "opencode", "pi": "pi",
                 "agent": "cursor", "cursor-agent": "cursor", "copilot": "copilot",
                 "agy": "antigravity", "/opt/x/bin/opencode.exe": "opencode"}
        for exe, runtime in names.items():
            with self.subTest(exe=exe):
                self.assertEqual(relaunch([exe])[0], runtime)

    def test_not_agents(self):
        for argv in (["bash"], ["/usr/bin/zsh", "-c", "claude"], ["node", "-e", "1"],
                     ["node", "/srv/app/server.js"], ["python3", "claude"], []):
            with self.subTest(argv=argv):
                self.assertIsNone(launch.identify(argv))


class NodeWrappedTest(unittest.TestCase):
    def test_script_basename(self):
        rt, argv, dropped = relaunch(
            ["node", "--no-warnings", "/home/u/.npm/bin/pi", "--continue", "--model", "m"])
        self.assertEqual(rt, "pi")
        self.assertEqual(argv, ["node", "--no-warnings", "/home/u/.npm/bin/pi", "--model", "m"])
        self.assertEqual(dropped, ["--continue"])

    def test_package_hint(self):
        rt, argv, _ = relaunch(
            ["/usr/bin/node", "/usr/lib/node_modules/@earendil-works/pi-coding-agent/"
             "dist/bundle/cli.js", "-r"])
        self.assertEqual(rt, "pi")
        self.assertEqual(argv[-1][-6:], "cli.js")

    def test_bun_run(self):
        rt, argv, _ = relaunch(["bun", "run", "/x/@anthropic-ai/claude-code/cli.js", "-c"])
        self.assertEqual((rt, argv), ("claude", ["bun", "run", "/x/@anthropic-ai/claude-code/cli.js"]))

    def test_cursor_launcher_exec_a(self):
        # The launcher runs `exec -a "$0" node --use-system-ca .../index.js "$@"`.
        rt, argv, dropped = relaunch(
            ["/home/u/.local/bin/agent", "--use-system-ca",
             "/home/u/.local/share/cursor-agent/versions/2026.10.14/index.js",
             "--resume", "c1", "--force"])
        self.assertEqual(rt, "cursor")
        self.assertEqual(argv, ["/home/u/.local/bin/agent", "--force"])
        self.assertEqual(dropped, ["--resume", "c1"])


def _table(*rows):
    """pid 1 is init; each row is (pid, ppid, argv)."""
    table = {1: (0, ["/sbin/init"], "/")}
    for pid, ppid, argv in rows:
        table[pid] = (ppid, argv, f"/work/{pid}")
    return launch.TableProcs(table)


def _run(procs, start):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = launch.main(True, procs=procs, start=start)
    return code, json.loads(out.getvalue()), err.getvalue()


class AncestorWalkTest(unittest.TestCase):
    def test_nearest_agent(self):
        procs = _table((10, 1, ["systemd", "--user"]),
                       (20, 10, ["/bin/fish", "-l"]),
                       (30, 20, ["claude", "--dangerously-skip-permissions", "--resume", SID]),
                       (40, 30, ["/usr/bin/zsh", "-c", "python3 ctxcheck launch-command"]))
        code, rec, _err = _run(procs, 40)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(rec["runtime"], "claude")
        self.assertEqual(rec["pid"], 30)
        self.assertEqual(rec["argv"], ["claude", "--dangerously-skip-permissions"])
        self.assertEqual(rec["shell_command"], "claude --dangerously-skip-permissions")
        self.assertEqual(rec["cwd"], "/work/30")
        self.assertEqual(rec["stripped"], ["--resume", SID])

    def test_no_agent_exits_3(self):
        procs = _table((20, 1, ["/bin/bash", "-l"]), (40, 20, ["sh", "-c", "ctxcheck"]))
        code, rec, err = _run(procs, 40)
        self.assertEqual(code, EXIT_UNMEASURABLE)
        self.assertFalse(rec["found"])
        self.assertIn("no agent CLI", err)

    def test_native_windows_exits_3(self):
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(launch.sys, "platform", "win32"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = launch.main(True)
        self.assertEqual(code, EXIT_UNMEASURABLE)
        self.assertFalse(json.loads(out.getvalue())["found"])
        self.assertIn("native Windows", err.getvalue())

    def test_node_wrapped_agent(self):
        # npm's loader spawns the native binary: the outer loader is what the
        # shell ran, so it is the one to relaunch.
        procs = _table((20, 1, ["/bin/bash"]),
                       (30, 20, ["node", "/usr/lib/node_modules/@github/copilot/npm-loader.js",
                                 "--resume=s1", "--model", "gpt-5"]),
                       (31, 30, ["/usr/lib/node_modules/@github/copilot-linux-x64/copilot",
                                 "--resume=s1", "--model", "gpt-5"]),
                       (40, 31, ["bash", "-c", "ctxcheck"]))
        code, rec, _err = _run(procs, 40)
        self.assertEqual(code, EXIT_OK)
        self.assertEqual((rec["runtime"], rec["pid"]), ("copilot", 30))
        self.assertEqual(rec["argv"], ["node", "/usr/lib/node_modules/@github/copilot/npm-loader.js",
                                       "--model", "gpt-5"])

    def test_nested_agents_take_the_nearest(self):
        # A Codex launched from a Claude shell: Codex is the agent running us.
        procs = _table((30, 1, ["claude"]), (35, 30, ["zsh"]),
                       (36, 35, ["codex", "--no-daemon"]), (40, 36, ["bash", "-c", "x"]))
        code, rec, _err = _run(procs, 40)
        self.assertEqual((code, rec["runtime"], rec["pid"]), (EXIT_OK, "codex", 36))

    def test_shell_command_quotes(self):
        procs = _table((30, 1, ["claude", "--append-system-prompt", "be brief; don't guess"]),
                       (40, 30, ["sh"]))
        _code, rec, _err = _run(procs, 40)
        self.assertEqual(rec["shell_command"],
                         "claude --append-system-prompt 'be brief; don'\"'\"'t guess'")

    def test_cycle_and_missing_pids_end_the_walk(self):
        procs = launch.TableProcs({40: (41, ["sh"], None), 41: (40, ["sh"], None)})
        code, _rec, _err = _run(procs, 40)
        self.assertEqual(code, EXIT_UNMEASURABLE)


@unittest.skipUnless(Path("/proc/self/stat").exists(), "Linux /proc only")
class LinuxProcsTest(unittest.TestCase):
    def test_reads_a_synthetic_proc_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, ppid, argv in ((7, 1, ["claude", "-c"]), (8, 7, ["sh", "-c", "x y"])):
                d = root / str(pid)
                d.mkdir()
                # comm with spaces and parens must not shift the fields
                (d / "stat").write_text(f"{pid} (a (b) c) S {ppid} {pid} {pid} 0 -1\n")
                (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
                os.symlink(tmp, d / "cwd")
            procs = launch.LinuxProcs(tmp)
            self.assertEqual(procs.parent(8), 7)
            self.assertEqual(procs.argv(8), ["sh", "-c", "x y"])
            self.assertIsNone(procs.argv(9))
            rec = launch.find(8, procs)
        self.assertEqual((rec["pid"], rec["argv"], rec["cwd"]), (7, ["claude"], tmp))

    def test_live_self(self):
        procs = launch.LinuxProcs()
        self.assertEqual(procs.parent(os.getpid()), os.getppid())
        self.assertTrue(procs.argv(os.getpid()))


class CliTest(unittest.TestCase):
    def test_cli_runs_the_walk(self):
        """Through the real entry point the walk starts at the test runner's
        shell, so the result depends on who runs the tests; only the shape is
        checked."""
        with tempfile.TemporaryDirectory() as tmp:
            r = helpers.run_ctxcheck(["launch-command", "--json"], helpers.clean_env(tmp))
        self.assertIn(r.returncode, (EXIT_OK, EXIT_UNMEASURABLE), r.stderr)
        rec = json.loads(r.stdout)
        if r.returncode == EXIT_OK:
            self.assertLessEqual({"runtime", "argv", "shell_command", "cwd", "pid"}, set(rec))


if __name__ == "__main__":
    unittest.main()
