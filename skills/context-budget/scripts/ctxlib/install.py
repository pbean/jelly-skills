"""Opt-in installers that edit an agent's own settings file.

`ctxcheck install-hook R [--dry-run|--uninstall]` adds the post-tool backstop
(`ctxcheck --hook --runtime R`) to runtime R's hooks file. Claude needs no
installer: the context-budget skill's own frontmatter hook registers when the
skill is invoked.

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

CTXCHECK = Path(__file__).resolve().parent.parent / "ctxcheck"
PYTHON = "python3"  # not sys.executable: the command must survive Python upgrades


class Target(NamedTuple):
    path: Callable[[], Path]
    # Create the file when missing? Cursor refuses a cli-config.json lacking
    # its required keys, so we never write a fresh one for it.
    may_create: bool
    note: str


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
           plan: Callable[[Optional[str], Path], tuple[str, str]],
           dry_run: bool, uninstall: bool) -> int:
    """Read the target file, plan the edit, then diff (dry run) or write it."""
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
        sys.stdout.write(_diff(path, old, new))
        print(f"(dry run: {path} not written; would have {what})")
        if not uninstall and runtime in HOOK_TARGETS:
            print(target.note)
        return EXIT_OK
    backup = _write(path, old, new)
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

# verified 2026-10-15: https://learn.chatgpt.com/docs/hooks (Codex 0.162.1).
# User hooks live in $CODEX_HOME/hooks.json (default ~/.codex) as
#   {"hooks": {"PostToolUse": [{"matcher": "<regex>", "hooks": [
#       {"type": "command", "command": "...", "timeout": <seconds>}]}]}}
# Shell commands match as "Bash" (codex-rs/core/src/tools/hook_names.rs). A
# new or changed hook is skipped until the user trusts it (/hooks); trust is
# keyed by <file>:<event>:<group index>:<hook index> in config.toml, so the
# installer appends a group last and never shifts the indices of existing
# (e.g. Orca's) hooks.
HOOK_TARGETS = {
    "codex": Target(
        lambda: _home("CODEX_HOME", ".codex") / "hooks.json",
        True, "Codex skips a new hook until you trust it: start Codex, run /hooks, "
              "and trust the ctxcheck PostToolUse hook."),
}


def _hook_group(runtime: str) -> dict:
    return {"matcher": "Bash", "hooks": [
        {"type": "command", "command": hook_command(runtime), "timeout": HOOK_TIMEOUT}]}


def _find_ours(text: str, groups: jsonedit.Node) -> list[tuple[int, int, jsonedit.Node]]:
    """(group index, hook index, command node) for each ctxcheck hook."""
    found = []
    for gi, group in enumerate(groups.items):
        hooks = group.get("hooks") if group.kind == "object" else None
        if not hooks or hooks.value.kind != "array":
            continue
        for hi, h in enumerate(hooks.value.items):
            cmd = h.get("command") if h.kind == "object" else None
            value = _string(text, cmd.value) if cmd else None
            if value and is_our_hook(value):
                found.append((gi, hi, cmd.value))
    return found


def plan_hooks_json(runtime: str, text: Optional[str], path: Path, event: str,
                    uninstall: bool) -> tuple[str, str]:
    """(new text, what happened) for a {"hooks": {event: [groups]}} file."""
    if text is None:
        if uninstall:
            return "", "not installed (no hooks file)"
        text = "{}\n"
    top = _parse(path, text)
    hooks_m = top.get("hooks")
    if hooks_m is not None and hooks_m.value.kind != "object":
        raise CtxError(f"{path}: hooks is not an object; edit it by hand")
    event_m = hooks_m.value.get(event) if hooks_m else None
    if event_m is not None and event_m.value.kind != "array":
        raise CtxError(f"{path}: hooks.{event} is not an array; edit it by hand")
    ours = _find_ours(text, event_m.value) if event_m else []

    if uninstall:
        if not ours:
            return text, "not installed"
        # Remove one at a time, re-parsing: spans shift after each edit.
        while ours:
            gi, hi, _ = ours[-1]
            group = event_m.value.items[gi]
            hooks = group.get("hooks").value
            if len(hooks.items) == 1 and set(m.key for m in group.members) <= {
                    "matcher", "hooks"}:
                if len(event_m.value.items) == 1:
                    text = (jsonedit.remove_member(text, hooks_m.value, event)
                            if len(hooks_m.value.members) > 1
                            else jsonedit.remove_member(text, top, "hooks"))
                else:
                    text = jsonedit.remove_item(text, event_m.value, gi)
            else:
                text = jsonedit.remove_item(text, hooks, hi)
            top = jsonedit.parse(text)
            hooks_m = top.get("hooks")
            event_m = hooks_m.value.get(event) if hooks_m else None
            ours = _find_ours(text, event_m.value) if event_m else []
        return text, f"removed the ctxcheck {event} hook"

    want = hook_command(runtime)
    if ours:
        stale = [node for _, _, node in ours if _string(text, node) != want]
        if not stale:
            return text, "already installed"
        for node in sorted(stale, key=lambda n: n.start, reverse=True):
            text = jsonedit.set_value(text, node, want)
        return text, "updated the ctxcheck path in the hook command"
    if hooks_m is None:
        return (jsonedit.add_member(text, top, "hooks", {event: [_hook_group(runtime)]}),
                f"added hooks.{event} with the ctxcheck hook")
    if event_m is None:
        return (jsonedit.add_member(text, hooks_m.value, event, [_hook_group(runtime)]),
                f"added hooks.{event} with the ctxcheck hook")
    return (jsonedit.append_item(text, event_m.value, _hook_group(runtime)),
            f"appended the ctxcheck hook to hooks.{event}")


def install_hook(runtime: str, dry_run: bool = False, uninstall: bool = False) -> int:
    if runtime == "claude":
        raise CtxError("Claude needs no installer: invoking the context-budget skill "
                       "registers its PostToolUse hook for the rest of the session")
    if runtime not in HOOK_TARGETS:
        raise CtxError(f"install-hook {runtime}: not implemented in this version "
                       f"(supported: {', '.join(sorted(HOOK_TARGETS))})")
    return _apply(runtime, HOOK_TARGETS[runtime],
                  lambda old, path: plan_hooks_json(runtime, old, path, "PostToolUse",
                                                    uninstall),
                  dry_run, uninstall)
