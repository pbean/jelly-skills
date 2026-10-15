"""launch-command: the command line that starts a fresh session of this agent.

A coordinator that hands off launches its successor in a new terminal. Orca
types that terminal's --command into a login shell, so the agent's launcher
defaults don't apply, and Orca exposes only the agent's identity, not its
argv. So the agent's own argv is read back from the process tree.

Walk up from ctxcheck's parent (Linux: /proc/<pid>/{stat,cmdline,cwd};
macOS: `ps -o ppid=,args= -p <pid>`) to the first process that is a known
agent CLI, run directly or through node/bun/deno. When its parent is the same
agent (an npm loader spawning the native binary), take the outermost one,
which is what the shell ran. Then strip what would resume or re-prompt the
old session: resume/continue/fork arguments and the initial prompt. What
remains keeps the launch posture (permission mode, sandbox, model).

    {runtime, argv, shell_command, cwd, pid, stripped}

Exit 3 when no ancestor is an agent CLI.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Mapping, NamedTuple, Sequence

from . import EXIT_ERROR, EXIT_OK, EXIT_UNMEASURABLE

MAX_DEPTH = 64


class Spec(NamedTuple):
    """How one agent CLI parses its argv, as far as stripping needs to know.

    Flags in `value` take the next token as their value; `variadic` ones take
    every following non-option token; `optional` ones take the next token
    unless it starts with "-". The drop_* sets are the same shapes, removed
    along with their values. `--flag=value` is one token in every case."""
    runtime: str
    names: tuple = ()               # executable / script basenames
    hints: tuple = ()               # package path fragments of a node script
    value: frozenset = frozenset()
    variadic: frozenset = frozenset()
    optional: frozenset = frozenset()
    drop: frozenset = frozenset()
    drop_value: frozenset = frozenset()
    drop_optional: frozenset = frozenset()
    resume_subcommands: frozenset = frozenset()
    keep_positionals: bool = False  # opencode: the positional is a project dir
    unknown_takes_value: bool = False  # pi: an unknown --x takes a non-dash, non-@ value
    single_dash_long: bool = False  # Go flag package: -model == --model
    stop_at_positional: bool = False  # Go flag package: options end at the first positional


def _f(*flags: str) -> frozenset:
    return frozenset(flags)


SPECS = (
    # verified 2026-10-15: `claude --help`, Claude Code 2.1.301 (Commander).
    Spec("claude", names=("claude",), hints=("/@anthropic-ai/claude-code/",),
         value=_f("--agent", "--agents", "--append-system-prompt", "--autocompact",
                  "--debug-file", "--effort", "--environment", "--fallback-model",
                  "--input-format", "--json-schema", "--max-budget-usd", "--model", "-n",
                  "--name", "--output-format", "--permission-mode", "--permission-prompts",
                  "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix",
                  "--setting-sources", "--settings", "--system-prompt",
                  "--system-prompt-snapshot"),
         variadic=_f("--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools",
                     "--disallowed-tools", "--betas", "--file", "--mcp-config", "--tools"),
         optional=_f("-d", "--debug", "-w", "--worktree", "--remote-control",
                     "--prompt-suggestions"),
         drop=_f("-c", "--continue", "--fork-session"),
         drop_value=_f("--session-id"),
         drop_optional=_f("-r", "--resume", "--from-pr", "--teleport")),
    # verified 2026-10-15: `codex --help`, `codex resume|fork --help`, codex-cli
    # 0.162.1 (clap). Resume and fork are subcommands; -c is --config here.
    Spec("codex", names=("codex",), hints=("/@openai/codex/",),
         value=_f("-c", "--config", "--enable", "--disable", "--remote",
                  "--remote-auth-token-env", "-m", "--model", "--local-provider", "-p",
                  "--profile", "-s", "--sandbox", "-C", "--cd", "--add-dir", "-a",
                  "--ask-for-approval"),
         variadic=_f("-i", "--image"),
         drop=_f("--last", "--all", "--include-non-interactive"),
         resume_subcommands=_f("resume", "fork")),
    # verified 2026-10-15: opencode.ai/docs/cli (not installed here). The TUI's
    # positional is a project directory; its initial prompt is --prompt.
    Spec("opencode", names=("opencode",), hints=("/opencode-ai/",),
         value=_f("-m", "--model", "--agent", "--port", "--hostname", "--mdns-domain",
                  "--cors", "--log-level"),
         drop=_f("-c", "--continue", "--fork"),
         drop_value=_f("-s", "--session", "--prompt"),
         keep_positionals=True),
    # verified 2026-10-15: @earendil-works/pi-coding-agent 1.3.0 docs/cli.md and
    # dist/cli/args.js (not installed here). Every positional is a message or
    # an @file; --flag=value is not a built-in form.
    Spec("pi", names=("pi",), hints=("/pi-coding-agent/",),
         value=_f("--mode", "--provider", "--model", "--api-key", "--thinking", "--models",
                  "-t", "--tools", "-xt", "--exclude-tools", "-e", "--extension", "--skill",
                  "--prompt-template", "--theme", "--use-theme", "--tui-mode",
                  "--system-prompt", "--append-system-prompt", "--session-dir", "-n",
                  "--name"),
         drop=_f("-c", "--continue", "-r", "--resume"),
         drop_value=_f("--session", "--session-id", "--fork"),
         unknown_takes_value=True),
    # verified 2026-10-15: cursor.com/docs/cli/reference/parameters and the
    # 2026.10.14 package's index.js (not installed here). `agent` is the
    # primary name, `cursor-agent` the legacy one; -c is --cloud, not continue.
    Spec("cursor", names=("cursor-agent", "agent"), hints=("/cursor-agent/",),
         value=_f("--api-key", "-H", "--header", "--statsig-overrides", "--auth-token", "-e",
                  "--endpoint", "--website-url", "--agent-endpoint", "--base-url",
                  "--local-agent-api-key", "--http-version", "--repo-endpoint",
                  "--repo-http-version", "--output-format", "--background-shell-timeout",
                  "--image", "--mode", "--model", "--dev-raw-model-slug", "--system-prompt",
                  "--conversation-history-file", "--harness", "--computer-use-coords",
                  "--allowed-tools", "--exclude-tools", "--data-dir", "--sandbox",
                  "--workspace", "--add-dir", "--plugin-dir", "--worktree-base",
                  "--min-version", "--format"),
         optional=_f("-w", "--worktree"),
         drop=_f("--continue"),
         drop_value=_f("--new-session-id", "--conversation-group-id"),
         drop_optional=_f("--resume"),
         resume_subcommands=_f("resume")),
    # verified 2026-10-15: docs.github.com/en/copilot/reference/cli-command-reference,
    # Copilot CLI 1.0.98 (not installed here). The initial prompt is -p/-i.
    Spec("copilot", names=("copilot",), hints=("/@github/copilot/",),
         value=_f("--add-dir", "--add-github-mcp-tool", "--add-github-mcp-toolset",
                  "--additional-mcp-config", "--agent", "--allow-tool", "--allow-url",
                  "--attachment", "-C", "--context", "--config-dir", "--deny-tool",
                  "--deny-url", "--disable-mcp-server", "--effort", "--reasoning-effort",
                  "--enable-mcp-server", "--excluded-tools", "--available-tools",
                  "--extension-sdk-path", "--log-dir", "--log-level", "--max-ai-credits",
                  "--max-autopilot-continues", "--mode", "--model", "-n", "--name",
                  "--output-format", "--plugin-dir", "--secret-env-vars", "--share",
                  "--stream"),
         optional=_f("--mouse", "-w", "--worktree"),
         drop=_f("--continue"),
         drop_value=_f("--session-id", "-p", "--prompt", "-i", "--interactive"),
         drop_optional=_f("-r", "--resume", "--connect")),
    # verified 2026-10-15: `agy --help`, Antigravity CLI 1.3.2 (Go flag package).
    # The prompt is the value of -p/-i; options end at the first positional.
    Spec("antigravity", names=("agy",),
         value=_f("--add-dir", "--agent", "--effort", "--input-format", "--json-schema",
                  "--log-file", "--mode", "--model", "--output-format", "--project",
                  "--print-timeout"),
         drop=_f("-c", "--continue"),
         drop_value=_f("--conversation", "-p", "--print", "--prompt", "-i",
                       "--prompt-interactive"),
         single_dash_long=True, stop_at_positional=True),
)

INTERPRETER = re.compile(r"^(node|nodejs|bun|deno)[0-9.]*(\.exe)?$")
# Interpreter options whose value is the next token.
INTERPRETER_VALUE = _f("-r", "--require", "--import", "--loader", "--experimental-loader",
                       "--conditions", "-C", "--title", "--env-file")
SCRIPT_SUFFIX = re.compile(r"\.(c|m)?(j|t)s$")


def _basename(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".exe") else name


def _spec_named(name: str) -> Spec | None:
    for spec in SPECS:
        if name in spec.names:
            return spec
    return None


def _script_index(argv: Sequence[str]) -> int | None:
    """Index of the script an interpreter runs, skipping its own options."""
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok in ("-e", "--eval", "-p", "--print"):
            return None  # inline code, no script
        if tok in INTERPRETER_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if tok == "run" and _basename(argv[0]).startswith(("bun", "deno")):
            i += 1
            continue
        return i
    return None


def identify(argv: Sequence[str]) -> tuple[Spec, list[str], list[str]] | None:
    """(spec, command prefix, agent arguments) when argv runs an agent CLI.

    The prefix is what relaunches the agent: the executable, or the
    interpreter with its options and the script."""
    if not argv:
        return None
    exe = _basename(argv[0])
    if INTERPRETER.match(exe):
        i = _script_index(argv)
        if i is None:
            return None
        script = argv[i].replace("\\", "/")
        spec = _spec_named(SCRIPT_SUFFIX.sub("", _basename(script)))
        if spec is None:
            spec = next((s for s in SPECS if any(h in script for h in s.hints)), None)
        return (spec, list(argv[:i + 1]), list(argv[i + 1:])) if spec else None
    spec = _spec_named(exe)
    if spec is None:
        return None
    if spec.runtime == "cursor":
        # The cursor-agent launcher runs `exec -a "$0" node [--use-system-ca]
        # .../index.js "$@"`, so argv[0] is the agent's name and its own
        # arguments start after the script.
        for i, tok in enumerate(argv[1:4], 1):
            if not tok.startswith("-"):
                if _basename(tok) == "index.js":
                    return spec, [argv[0]], list(argv[i + 1:])
                break
    return spec, [argv[0]], list(argv[1:])


def strip(spec: Spec, args: Sequence[str]) -> tuple[list[str], list[str]]:
    """(kept, stripped) agent arguments: no resume/continue/fork, no prompt."""
    kept: list[str] = []
    dropped: list[str] = []
    i = 0
    n = len(args)

    def nxt_is_value(j: int) -> bool:
        return j < n and not args[j].startswith("-")

    while i < n:
        tok = args[i]
        if tok == "--":  # the rest is positional: a prompt, files, a message
            dropped.extend(args[i:])
            break
        if not tok.startswith("-") or tok == "-":
            if spec.keep_positionals and tok not in spec.resume_subcommands:
                kept.append(tok)
            else:
                dropped.append(tok)
            if spec.stop_at_positional:
                dropped.extend(args[i + 1:])
                break
            i += 1
            continue
        name, eq, _ = tok.partition("=")
        if spec.single_dash_long and not name.startswith("--") and len(name) > 2:
            name = "-" + name
        inline = bool(eq)

        if name in spec.drop:
            dropped.append(tok)
            i += 1
        elif name in spec.drop_value:
            take = 1 if inline or i + 1 >= n else 2
            dropped.extend(args[i:i + take])
            i += take
        elif name in spec.drop_optional:
            take = 1 if inline or not nxt_is_value(i + 1) else 2
            dropped.extend(args[i:i + take])
            i += take
        elif name in spec.value:
            take = 1 if inline or i + 1 >= n else 2
            kept.extend(args[i:i + take])
            i += take
        elif name in spec.optional:
            take = 1 if inline or not nxt_is_value(i + 1) else 2
            kept.extend(args[i:i + take])
            i += take
        elif name in spec.variadic:
            kept.append(tok)
            i += 1
            if not inline:
                while nxt_is_value(i):
                    kept.append(args[i])
                    i += 1
        elif (spec.unknown_takes_value and name.startswith("--") and not inline
              and i + 1 < n and not args[i + 1].startswith(("-", "@"))):
            kept.extend(args[i:i + 2])
            i += 2
        else:  # a boolean flag, or one this table doesn't know: keep it alone
            kept.append(tok)
            i += 1
    return kept, dropped


class Procs:
    """The process table. Each method returns None when the pid is gone."""

    def parent(self, pid: int) -> int | None:
        raise NotImplementedError

    def argv(self, pid: int) -> list[str] | None:
        raise NotImplementedError

    def cwd(self, pid: int) -> str | None:
        raise NotImplementedError


class LinuxProcs(Procs):
    def __init__(self, root: str = "/proc") -> None:
        self.root = Path(root)

    def parent(self, pid: int) -> int | None:
        try:
            stat = (self.root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
            # comm may contain spaces and parens; fields resume after the last ")".
            return int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    def argv(self, pid: int) -> list[str] | None:
        try:
            raw = (self.root / str(pid) / "cmdline").read_bytes()
        except OSError:
            return None
        return [a.decode("utf-8", "surrogateescape") for a in raw.split(b"\0")[:-1]] or None

    def cwd(self, pid: int) -> str | None:
        try:
            return os.readlink(self.root / str(pid) / "cwd")
        except OSError:
            return None


class PsProcs(Procs):
    """macOS / BSD. `ps` joins argv with spaces, so an argument that itself
    contains a space can't be recovered; such argv come back split."""

    def _ps(self, pid: int) -> tuple[int, list[str]] | None:
        try:
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL).stdout.strip()
            ppid, _, args = out.partition(" ")
            return int(ppid), args.split()
        except (OSError, ValueError, subprocess.SubprocessError):
            return None

    def parent(self, pid: int) -> int | None:
        got = self._ps(pid)
        return got[0] if got else None

    def argv(self, pid: int) -> list[str] | None:
        got = self._ps(pid)
        return got[1] if got and got[1] else None

    def cwd(self, pid: int) -> str | None:
        try:
            out = subprocess.run(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                                 capture_output=True, text=True, timeout=10,
                                 stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return next((ln[1:] for ln in out.splitlines() if ln.startswith("n")), None)


class TableProcs(Procs):
    """A fixed table {pid: (ppid, argv, cwd)}; for tests and snapshots."""

    def __init__(self, table: Mapping[int, tuple]) -> None:
        self.table = dict(table)

    def parent(self, pid: int) -> int | None:
        row = self.table.get(pid)
        return row[0] if row else None

    def argv(self, pid: int) -> list[str] | None:
        row = self.table.get(pid)
        return list(row[1]) if row else None

    def cwd(self, pid: int) -> str | None:
        row = self.table.get(pid)
        return row[2] if row and len(row) > 2 else None


def default_procs() -> Procs:
    return LinuxProcs() if Path("/proc/self/cmdline").exists() else PsProcs()


def find(start: int, procs: Procs) -> dict | None:
    """The launch record for the nearest agent ancestor of `start` (inclusive)."""
    pid: int | None = start
    seen: set[int] = set()
    found = None
    for _ in range(MAX_DEPTH):
        if pid is None or pid <= 0 or pid in seen:
            break
        seen.add(pid)
        argv = procs.argv(pid)
        hit = identify(argv) if argv else None
        if found is None:
            if hit:
                found = (pid, argv, hit)
        elif hit and hit[0].runtime == found[2][0].runtime:
            found = (pid, argv, hit)  # an outer loader of the same agent
        else:
            break
        pid = procs.parent(pid)
    if found is None:
        return None
    pid, argv, (spec, prefix, args) = found
    kept, dropped = strip(spec, args)
    out = prefix + kept
    return {
        "runtime": spec.runtime,
        "argv": out,
        "shell_command": shlex.join(out),
        "cwd": procs.cwd(pid),
        "pid": pid,
        "stripped": dropped,
    }


def main(as_json: bool, procs: Procs | None = None, start: int | None = None) -> int:
    procs = procs or default_procs()
    start = os.getppid() if start is None else start
    try:
        rec = find(start, procs)
    except Exception as e:  # noqa: BLE001 - an unreadable table is an error, not a crash
        print(f"ctxcheck launch-command: {e}", file=sys.stderr)
        return EXIT_ERROR
    if rec is None:
        msg = ("no agent CLI among this process's ancestors (looked for "
               + ", ".join(n for s in SPECS for n in s.names) + ")")
        if as_json:
            print(json.dumps({"runtime": None, "found": False, "message": msg}, indent=2))
        print(f"ctxcheck: {msg}", file=sys.stderr)
        return EXIT_UNMEASURABLE
    if as_json:
        print(json.dumps(rec, indent=2))
    else:
        print(rec["shell_command"])
    return EXIT_OK
