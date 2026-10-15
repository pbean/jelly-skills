"""Opt-in installers that edit an agent's own settings file.

`ctxcheck install-hook R [--dry-run|--uninstall]` adds the backstop
(`ctxcheck --hook --runtime R`) to runtime R's hooks file: Codex, Cursor and
Copilot after each tool call, Antigravity before each model call. Claude
needs no installer: the context-budget skill's own frontmatter hook
registers when the skill is invoked.

`ctxcheck install-statusline R [--dry-run|--uninstall]` points runtime R's
status line at `ctxcheck statusline-tee`, wrapping whatever command was there:

    python3 <abs ctxcheck> statusline-tee --runtime R -- '<original command>'

The original is shell-quoted into one argument, so the tee runs it through
the shell exactly as the agent did (pipes, `~` and all) and uninstall can
recover it verbatim.

Rules, shared by every installer:
  * Only the bytes that change are rewritten (jsonedit); key order,
    indentation and everything else in the file stay as they were.
  * Before any write, the file is copied to `<file>.ctxcheck-<UTC stamp>.bak`,
    and the new text is written to a temp file and renamed into place.
  * Re-running is a no-op once installed (a stale ctxcheck path is updated).
  * --dry-run prints a unified diff and writes nothing.
  * --uninstall puts the original command back in place (or removes what
    install added); it does not restore the backup wholesale, so edits made
    since the install survive.
  * A file that isn't strict JSON (comments, trailing commas) is refused.
"""

from __future__ import annotations

import difflib
import os
import shlex
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from . import EXIT_OK, CtxError
from . import jsonedit
from .hook import ANTIGRAVITY_EVENT

CTXCHECK = Path(__file__).resolve().parent.parent / "ctxcheck"
PYTHON = "python3"  # not sys.executable: the command must survive Python upgrades


class Target(NamedTuple):
    path: Callable[[], Path]
    # Create the file when missing? Cursor refuses a cli-config.json lacking
    # its required keys, so we never write a fresh one for it.
    may_create: bool
    note: str
    # A file only ctxcheck writes: uninstall deletes it once nothing else is
    # left in it, instead of leaving an empty shell behind.
    owned: bool = False


def _home(env: str, default: str) -> Path:
    v = os.environ.get(env)
    return Path(v).expanduser() if v else Path.home() / default


# verified 2026-10-15 - see the payload notes in backends/statusline.py.
STATUSLINE_TARGETS = {
    # https://www.antigravity.google/docs/cli/statusline (agy 1.3.2):
    # {"statusLine": {"type": "command", "command": "...", padding?, enabled?,
    #  stack_with_default?}} in ~/.gemini/antigravity-cli/settings.json.
    "antigravity": Target(
        lambda: Path.home() / ".gemini" / "antigravity-cli" / "settings.json",
        True, "Antigravity re-reads it on the next status refresh."),
    # docs.github.com/en/copilot/reference/copilot-cli-reference/
    # cli-config-dir-reference (Copilot CLI 1.0.98): user settings live only
    # in settings.json; COPILOT_HOME replaces the whole ~/.copilot path.
    # {"statusLine": {"type"?: "command", "command": "...", padding?,
    #  refreshInterval?}}. The command runs through a shell, 10 s timeout.
    "copilot": Target(
        lambda: _home("COPILOT_HOME", ".copilot") / "settings.json",
        True, "Restart Copilot CLI to pick it up."),
    # https://cursor.com/docs/cli/reference/configuration: global config
    # ~/.cursor/cli-config.json, dir overridable by CURSOR_CONFIG_DIR;
    # statusLine itself is COMMUNITY-sourced (Cursor's built-in statusline
    # skill). Cursor may rewrite this file when a session exits: install with
    # no session running.
    "cursor": Target(
        lambda: _home("CURSOR_CONFIG_DIR", ".cursor") / "cli-config.json",
        False, "Install with no Cursor session running: an exiting session "
               "can rewrite cli-config.json."),
}


def wrap_command(runtime: str, original: Optional[str]) -> str:
    cmd = f"{PYTHON} {shlex.quote(str(CTXCHECK))} statusline-tee --runtime {runtime}"
    return f"{cmd} -- {shlex.quote(original)}" if original else cmd


class Wrapped(NamedTuple):
    ctxcheck: str
    runtime: str
    original: Optional[str]


def parse_wrap(command: str) -> Optional[Wrapped]:
    """Recognize a command written by wrap_command, from any install path."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if "statusline-tee" not in tokens:
        return None
    i = tokens.index("statusline-tee")
    if i < 1 or Path(tokens[i - 1]).name != "ctxcheck" or tokens[i + 1:i + 2] != ["--runtime"]:
        return None
    runtime = tokens[i + 2] if len(tokens) > i + 2 else ""
    rest = tokens[i + 3:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    elif rest:
        return None
    original = (rest[0] if len(rest) == 1 else shlex.join(rest)) if rest else None
    return Wrapped(tokens[i - 1], runtime, original)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise CtxError(f"cannot read {path}: {e}") from e


def _parse(path: Path, text: str) -> jsonedit.Node:
    try:
        top = jsonedit.parse(text)
    except ValueError as e:
        raise CtxError(f"{path} is not strict JSON ({e}); edit it by hand, or "
                       "remove comments and trailing commas first") from None
    if top.kind != "object":
        raise CtxError(f"{path}: expected a JSON object at the top level")
    return top


def _string(text: str, node: jsonedit.Node) -> Optional[str]:
    import json
    if node.kind != "string":
        return None
    return json.loads(text[node.start:node.end])


def _write(path: Path, old: Optional[str], new: str) -> Optional[Path]:
    """Back up `path` (if it exists), then replace it atomically."""
    backup = None
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    if old is not None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.ctxcheck-{stamp}.bak")
        n = 1
        while backup.exists():
            backup = path.with_name(f"{path.name}.ctxcheck-{stamp}-{n}.bak")
            n += 1
        shutil.copy2(path, backup)
        mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return backup


def _delete(path: Path) -> Path:
    """Back up `path`, then remove it."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = path.with_name(f"{path.name}.ctxcheck-{stamp}.bak")
    n = 1
    while backup.exists():
        backup = path.with_name(f"{path.name}.ctxcheck-{stamp}-{n}.bak")
        n += 1
    os.replace(path, backup)
    return backup


def _diff(path: Path, old: Optional[str], new: str) -> str:
    return "".join(difflib.unified_diff(
        (old or "").splitlines(True), new.splitlines(True),
        fromfile=str(path) if old is not None else "/dev/null", tofile=str(path)))


def plan_statusline(runtime: str, text: Optional[str], path: Path,
                    uninstall: bool) -> tuple[str, str]:
    """(new text, what happened). new == old text means nothing to do."""
    if text is None:
        if uninstall:
            return "", "not installed (no settings file)"
        text = "{}\n"
    top = _parse(path, text)
    member = top.get("statusLine")
    sl = member.value if member else None
    if sl is not None and sl.kind != "object":
        raise CtxError(f"{path}: statusLine is not an object; edit it by hand")
    cmd_member = sl.get("command") if sl else None
    current = _string(text, cmd_member.value) if cmd_member else None
    if cmd_member and current is None:
        raise CtxError(f"{path}: statusLine.command is not a string; edit it by hand")
    wrapped = parse_wrap(current) if current else None
    if wrapped and wrapped.runtime != runtime:
        raise CtxError(f"{path}: statusLine already tees for runtime "
                       f"{wrapped.runtime!r}, not {runtime!r}; uninstall that first")

    if uninstall:
        if not wrapped:
            return text, "not installed"
        if wrapped.original:
            return (jsonedit.set_value(text, cmd_member.value, wrapped.original),
                    f"restored statusLine.command to {wrapped.original!r}")
        other = {m.key for m in sl.members} - {"type", "command"}
        type_m = sl.get("type")
        if not other and (type_m is None or _string(text, type_m.value) == "command"):
            return jsonedit.remove_member(text, top, "statusLine"), "removed statusLine"
        return jsonedit.remove_member(text, sl, "command"), "removed statusLine.command"

    if wrapped:
        want = wrap_command(runtime, wrapped.original)
        if current == want:
            return text, "already installed"
        return (jsonedit.set_value(text, cmd_member.value, want),
                "updated the ctxcheck path in statusLine.command")
    if sl is None:
        return (jsonedit.add_member(text, top, "statusLine",
                                    {"type": "command", "command": wrap_command(runtime, None)}),
                "added statusLine (no previous status line)")
    if cmd_member is None:
        return (jsonedit.add_member(text, sl, "command", wrap_command(runtime, None)),
                "added statusLine.command")
    return (jsonedit.set_value(text, cmd_member.value, wrap_command(runtime, current)),
            f"wrapped the existing status line {current!r}")


def install_statusline(runtime: str, dry_run: bool = False, uninstall: bool = False) -> int:
    if runtime not in STATUSLINE_TARGETS:
        raise CtxError(f"install-statusline supports {', '.join(sorted(STATUSLINE_TARGETS))}; "
                       f"{runtime} is measured from its own session data")
    return _apply(runtime, STATUSLINE_TARGETS[runtime],
                  lambda old, path: plan_statusline(runtime, old, path, uninstall),
                  dry_run, uninstall)


def _apply(runtime: str, target: Target,
           plan: Callable[[Optional[str], Path], tuple[Optional[str], str]],
           dry_run: bool, uninstall: bool) -> int:
    """Read the target file, plan the edit, then diff (dry run) or write it.
    A planned text of None means "delete the file" (an owned file, emptied)."""
    path = target.path()
    old = _read(path)
    if old is None and not uninstall and not target.may_create:
        raise CtxError(f"{path} not found; start {runtime} once so it writes its config, "
                       "then re-run")
    new, what = plan(old, path)
    if new == (old if old is not None else "") or (old is None and uninstall):
        print(f"{path}: {what}; nothing to change")
        return EXIT_OK
    if dry_run:
        if new is None:
            sys.stdout.write("".join(difflib.unified_diff(
                (old or "").splitlines(True), [], fromfile=str(path), tofile="/dev/null")))
        else:
            sys.stdout.write(_diff(path, old, new))
        print(f"(dry run: {path} not written; would have {what})")
        if not uninstall and runtime in HOOK_TARGETS:
            print(target.note)
        return EXIT_OK
    backup = _write(path, old, new) if new is not None else _delete(path)
    print(f"{path}: {what}")
    if backup:
        print(f"backup: {backup}")
    if not uninstall:
        print(target.note)
    return EXIT_OK


# --- post-tool hook installers -------------------------------------------

def hook_command(runtime: str) -> str:
    return f"{PYTHON} {shlex.quote(str(CTXCHECK))} --hook --runtime {runtime}"


def is_our_hook(command: str) -> bool:
    """A command written by hook_command, from any install path."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return "--hook" in tokens and any(Path(tok).name == "ctxcheck" for tok in tokens)


HOOK_TIMEOUT = 10  # seconds; ctxcheck --hook reads one transcript and exits


class HookFile(NamedTuple):
    """Where in a hooks file the backstop goes, and what it looks like."""
    event: str
    entry: Callable[[str], dict]  # hook command -> the entry install appends
    # Entries are Claude-style groups {"matcher", "hooks": [handlers]} (Codex);
    # else handlers sit directly in the event's array (Cursor, Copilot).
    grouped: bool = True
    skeleton: str = "{}\n"  # a fresh file, before the hooks key is added
    # Keys that may hold a handler's shell command.
    command_keys: tuple = ("command",)


def _grouped_entry(command: str) -> dict:
    return {"matcher": "Bash", "hooks": [
        {"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]}


# verified 2026-10-15: https://learn.chatgpt.com/docs/hooks (Codex 0.162.1).
# User hooks live in $CODEX_HOME/hooks.json (default ~/.codex) as
#   {"hooks": {"PostToolUse": [{"matcher": "<regex>", "hooks": [
#       {"type": "command", "command": "...", "timeout": <seconds>}]}]}}
# Shell commands match as "Bash" (codex-rs/core/src/tools/hook_names.rs). A
# new or changed hook is skipped until the user trusts it (/hooks); trust is
# keyed by <file>:<event>:<group index>:<hook index> in config.toml, so the
# installer appends a group last and never shifts the indices of existing
# (e.g. Orca's) hooks.
CODEX_HOOKS = HookFile("PostToolUse", _grouped_entry)

# verified 2026-10-15: https://cursor.com/docs/hooks.md. User hooks live in
# ~/.cursor/hooks.json (no documented override; CURSOR_CONFIG_DIR is
# documented for cli-config.json only) as
#   {"version": 1, "hooks": {"postToolUse": [{"command": "...",
#       "matcher": "<regex>", "timeout": <seconds>}]}}
# "version" is required (positive integer, use 1). postToolUse matchers see
# Shell, Read, Write, Grep, Delete, Task, MCP:<tool>; "Shell" mirrors the
# Bash matcher of the Claude and Codex backstops. User hooks run from
# ~/.cursor/, need no trust prompt, and the file is reloaded on save.
CURSOR_HOOKS = HookFile(
    "postToolUse",
    lambda command: {"command": command, "matcher": "Shell", "timeout": HOOK_TIMEOUT},
    grouped=False, skeleton='{\n  "version": 1\n}\n')

# verified 2026-10-15: https://docs.github.com/en/copilot/reference/
# hooks-configuration (Copilot CLI 1.0.98). User hooks are every *.json in
# ~/.copilot/hooks/ ("If COPILOT_HOME is set, it is $COPILOT_HOME/hooks/"),
#   {"version": 1, "hooks": {"postToolUse": [{"type": "command",
#       "bash": "...", "timeoutSec": <seconds>}]}}
# The camelCase key gets the camelCase payload (sessionId, toolName, ...).
# No matcher: the shell tool's name under postToolUse isn't documented. An
# invalid file is rejected whole, so ours is a separate ctxcheck.json. User
# hooks need no trust prompt ("workspace admission" is for repo hooks).
COPILOT_HOOKS = HookFile(
    "postToolUse",
    lambda command: {"type": "command", "bash": command, "timeoutSec": HOOK_TIMEOUT},
    grouped=False, skeleton='{\n  "version": 1\n}\n',
    command_keys=("bash", "command", "powershell"))

HOOK_TARGETS = {
    "codex": Target(
        lambda: _home("CODEX_HOME", ".codex") / "hooks.json",
        True, "Codex skips a new hook until you trust it: start Codex, run /hooks, "
              "and trust the ctxcheck PostToolUse hook."),
    "cursor": Target(
        lambda: Path.home() / ".cursor" / "hooks.json",
        True, "Cursor reloads hooks.json on save (restart Cursor if the hook "
              "doesn't load). User hooks need no trust prompt."),
    "copilot": Target(
        lambda: _home("COPILOT_HOME", ".copilot") / "hooks" / "ctxcheck.json",
        True, "Restart Copilot CLI so it loads the new hook (hot reload isn't "
              "documented). User hooks need no trust prompt.", owned=True),
    # verified 2026-10-15: agy 1.3.2's bundled ~/.gemini/antigravity-cli/
    # builtin/skills/agy-customizations/{SKILL.md,docs/hooks.md}: the global
    # customization root is ~/.gemini/config/, and hooks.json there maps a
    # hook NAME to its events. PreInvocation is flat (handlers directly):
    #   {"<name>": {"PreInvocation": [{"type": "command", "command": "...",
    #       "timeout": <seconds>}]}}
    # Commands run via `sh -c` in the hooks.json directory. Named hooks for
    # one event (e.g. Orca's) are merged and run in turn.
    "antigravity": Target(
        lambda: Path.home() / ".gemini" / "config" / "hooks.json",
        True, "Start a new agy session so it loads the new hook (hot reload isn't "
              "documented). No trust prompt is documented."),
}

HOOK_FILES = {"codex": CODEX_HOOKS, "cursor": CURSOR_HOOKS, "copilot": COPILOT_HOOKS}

# Antigravity: the top-level hook name ctxcheck owns. Its event is the one
# hook.py warns on (PostToolUse output must be {}).
ANTIGRAVITY_NAME = "ctxcheck"


def _command_of(text: str, handler: jsonedit.Node, keys: tuple) -> Optional[jsonedit.Node]:
    """The node of a ctxcheck handler's command, or None for anyone else's."""
    if handler.kind != "object":
        return None
    for key in keys:
        m = handler.get(key)
        value = _string(text, m.value) if m else None
        if value and is_our_hook(value):
            return m.value
    return None


def _find_ours(text: str, entries: jsonedit.Node, spec: HookFile
               ) -> list[tuple[int, Optional[int], jsonedit.Node]]:
    """(entry index, handler index or None if flat, command node) per ctxcheck hook."""
    found = []
    for gi, entry in enumerate(entries.items):
        if not spec.grouped:
            node = _command_of(text, entry, spec.command_keys)
            if node:
                found.append((gi, None, node))
            continue
        hooks = entry.get("hooks") if entry.kind == "object" else None
        if not hooks or hooks.value.kind != "array":
            continue
        for hi, h in enumerate(hooks.value.items):
            node = _command_of(text, h, spec.command_keys)
            if node:
                found.append((gi, hi, node))
    return found


def _locate(text: str, path: Path, event: str):
    top = _parse(path, text)
    hooks_m = top.get("hooks")
    if hooks_m is not None and hooks_m.value.kind != "object":
        raise CtxError(f"{path}: hooks is not an object; edit it by hand")
    event_m = hooks_m.value.get(event) if hooks_m else None
    if event_m is not None and event_m.value.kind != "array":
        raise CtxError(f"{path}: hooks.{event} is not an array; edit it by hand")
    return top, hooks_m, event_m


def plan_hooks_json(runtime: str, text: Optional[str], path: Path, spec: HookFile,
                    uninstall: bool, owned: bool = False) -> tuple[Optional[str], str]:
    """(new text, what happened) for a {"hooks": {event: [entries]}} file.
    New text None: delete the (owned) file."""
    event = spec.event
    if text is None:
        if uninstall:
            return "", "not installed (no hooks file)"
        text = spec.skeleton
    top, hooks_m, event_m = _locate(text, path, event)
    ours = _find_ours(text, event_m.value, spec) if event_m else []

    if uninstall:
        if not ours:
            return text, "not installed"
        # Remove one at a time, re-parsing: spans shift after each edit.
        while ours:
            gi, hi, _ = ours[-1]
            entry = event_m.value.items[gi]
            hooks = entry.get("hooks").value if hi is not None else None
            whole = hooks is None or (
                len(hooks.items) == 1
                and set(m.key for m in entry.members) <= {"matcher", "hooks"})
            if whole:
                if len(event_m.value.items) == 1:
                    text = (jsonedit.remove_member(text, hooks_m.value, event)
                            if len(hooks_m.value.members) > 1
                            else jsonedit.remove_member(text, top, "hooks"))
                else:
                    text = jsonedit.remove_item(text, event_m.value, gi)
            else:
                text = jsonedit.remove_item(text, hooks, hi)
            top, hooks_m, event_m = _locate(text, path, event)
            ours = _find_ours(text, event_m.value, spec) if event_m else []
        what = f"removed the ctxcheck {event} hook"
        if owned and text == spec.skeleton:
            return None, f"{what} and deleted the file (nothing else was in it)"
        return text, what

    want = hook_command(runtime)
    if ours:
        stale = [node for _, _, node in ours if _string(text, node) != want]
        if not stale:
            return text, "already installed"
        for node in sorted(stale, key=lambda n: n.start, reverse=True):
            text = jsonedit.set_value(text, node, want)
        return text, "updated the ctxcheck path in the hook command"
    entry = spec.entry(want)
    if hooks_m is None:
        return (jsonedit.add_member(text, top, "hooks", {event: [entry]}),
                f"added hooks.{event} with the ctxcheck hook")
    if event_m is None:
        return (jsonedit.add_member(text, hooks_m.value, event, [entry]),
                f"added hooks.{event} with the ctxcheck hook")
    return (jsonedit.append_item(text, event_m.value, entry),
            f"appended the ctxcheck hook to hooks.{event}")


def plan_antigravity(text: Optional[str], path: Path,
                     uninstall: bool) -> tuple[Optional[str], str]:
    """(new text, what happened) for Antigravity's {name: {event: [...]}} file:
    ctxcheck owns the top-level name ANTIGRAVITY_NAME and nothing else."""
    if text is None:
        if uninstall:
            return "", "not installed (no hooks file)"
        text = "{}\n"
    top = _parse(path, text)
    mine = top.get(ANTIGRAVITY_NAME)
    want = f"{hook_command('antigravity')} --event {ANTIGRAVITY_EVENT}"
    spec = {ANTIGRAVITY_EVENT: [
        {"type": "command", "command": want, "timeout": HOOK_TIMEOUT}]}
    if mine is None:
        if uninstall:
            return text, "not installed"
        return (jsonedit.add_member(text, top, ANTIGRAVITY_NAME, spec),
                f"added the {ANTIGRAVITY_NAME!r} hook ({ANTIGRAVITY_EVENT})")

    # The name is taken: it must be ours, exactly one handler on one event.
    node = mine.value
    handlers = node.get(ANTIGRAVITY_EVENT) if node.kind == "object" else None
    ours = (handlers is not None and handlers.value.kind == "array"
            and len(handlers.value.items) == 1
            and {m.key for m in node.members} <= {ANTIGRAVITY_EVENT, "enabled"}
            and _command_of(text, handlers.value.items[0], ("command",)))
    if not ours:
        raise CtxError(f"{path}: a hook named {ANTIGRAVITY_NAME!r} exists and isn't the "
                       "one ctxcheck installs; rename or remove it by hand")
    if uninstall:
        return (jsonedit.remove_member(text, top, ANTIGRAVITY_NAME),
                f"removed the {ANTIGRAVITY_NAME!r} hook")
    if _string(text, ours) == want:
        return text, "already installed"
    return jsonedit.set_value(text, ours, want), "updated the ctxcheck path in the hook command"


def install_hook(runtime: str, dry_run: bool = False, uninstall: bool = False) -> int:
    if runtime == "claude":
        raise CtxError("Claude needs no installer: invoking the context-budget skill "
                       "registers its PostToolUse hook for the rest of the session")
    if runtime not in HOOK_TARGETS:
        raise CtxError(f"install-hook {runtime}: not implemented in this version "
                       f"(supported: {', '.join(sorted(HOOK_TARGETS))})")
    target = HOOK_TARGETS[runtime]
    if runtime == "antigravity":
        def plan(old, path):
            return plan_antigravity(old, path, uninstall)
    else:
        def plan(old, path):
            return plan_hooks_json(runtime, old, path, HOOK_FILES[runtime], uninstall,
                                   target.owned)
    return _apply(runtime, target, plan, dry_run, uninstall)
