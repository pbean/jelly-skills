"""ctxcheck - report an agent session's own context-window usage.

Nothing in Orca knows how full an agent's context is. The agent CLIs do know,
but show it only to their own status line, never to the model. So the agent
reads it back from its own session data.

Usage:
    ctxcheck [--json] [--role coordinator|worker|planner] [--gate PCT]
             [--window N] [--preset NAME]
             [--runtime auto|claude|codex|opencode|pi|copilot|antigravity|cursor]
             [--session ID] [--transcript PATH]
    ctxcheck --list-presets
    ctxcheck --arm --role R --label "<free text>" [--gate PCT] [--window N]
    ctxcheck --disarm
    ctxcheck --hook [--runtime R]     (run by the agent's post-tool hook)
    ctxcheck install-hook codex [--dry-run|--uninstall]
    ctxcheck statusline-tee --runtime R [-- <wrapped status-line cmd...>]
    ctxcheck install-statusline cursor|copilot|antigravity [--dry-run|--uninstall]

Copilot CLI, Antigravity and Cursor CLI are measured from a status-line
sidecar: run install-statusline once, then keep the TUI on screen (snapshots
older than 15 minutes are refused with exit 3).

With --role, the gate is derived from the window (see --list-presets); --gate
overrides it. With neither, usage is reported with no threshold - unless the
session is armed: then a plain `ctxcheck` uses the role, gate and window
recorded by --arm. Arming also lets the post-tool hook warn the agent when it
goes over its gate (Claude: the context-budget skill's own hook; Codex:
install-hook codex). Disarm when the session's work ends.

Exit codes:
    0   ok, or at/under the gate
    1   error
    3   this runtime cannot be measured: you have no budget here; say so once
        and carry on
    10  gate exceeded

Env:
    CTXCHECK_WINDOW   window size in tokens; overrides discovery
    CTXCHECK_PRESET   preset used when nothing is discovered
    CTXCHECK_HOME     state dir (default $XDG_STATE_HOME/jelly-skills/ctxcheck)
    CODEX_HOME        Codex state root (default ~/.codex)
    XDG_DATA_HOME     OpenCode's opencode.db lives under it (default ~/.local/share)
    PI_CODING_AGENT_DIR, PI_CODING_AGENT_SESSION_DIR
                      Pi's agent and session dirs (default ~/.pi/agent[/sessions])
    COPILOT_HOME      Copilot CLI's config dir (default ~/.copilot)
    CURSOR_CONFIG_DIR Cursor CLI's config dir (default ~/.cursor)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import NoReturn, Sequence

from . import (EXIT_ERROR, EXIT_GATE_EXCEEDED, EXIT_OK, EXIT_UNMEASURABLE,
               CtxError, Unmeasurable)
from . import gates, hook, install, markers, runtime, tee

# Verbs the CLI shape reserves for later phases. They parse now so that
# callers and docs can be written against the final shape.
NOT_IMPLEMENTED = "not implemented in this version"
ALL_RUNTIMES = ("claude", "codex", "opencode", "pi", "cursor", "copilot", "antigravity")


class _Parser(argparse.ArgumentParser):
    """Usage errors exit 1 (ctxcheck's error code), not argparse's 2."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def _subcommand_parsers() -> dict[str, argparse.ArgumentParser]:
    subs: dict[str, argparse.ArgumentParser] = {}

    # statusline-tee parses its own arguments (tee.main): it must run the
    # wrapped command even when they are wrong.
    for verb, what, choices in (
            ("install-statusline", "wrap the runtime's status line in statusline-tee",
             tuple(sorted(install.STATUSLINE_TARGETS))),
            ("install-hook", "install the post-tool backstop that warns an armed "
             "session over its gate", ALL_RUNTIMES)):
        p = _Parser(prog=f"ctxcheck {verb}", description=what)
        p.add_argument("target_runtime", metavar="RUNTIME", choices=choices)
        g = p.add_mutually_exclusive_group()
        g.add_argument("--dry-run", action="store_true")
        g.add_argument("--uninstall", action="store_true")
        subs[verb] = p

    p = _Parser(prog="ctxcheck launch-command",
                description="print the command that relaunches this agent (P9)")
    p.add_argument("--json", action="store_true", dest="as_json")
    subs["launch-command"] = p
    return subs


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ctxcheck", description=__doc__,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", dest="as_json")
    p.add_argument("--role", choices=gates.ROLES,
                   help="derive the gate from the window for this role")
    p.add_argument("--gate", type=float, metavar="PCT",
                   help=f"exit {EXIT_GATE_EXCEEDED} when usage exceeds PCT percent")
    p.add_argument("--window", type=int, metavar="TOKENS",
                   help="context window size; overrides --preset and discovery")
    p.add_argument("--preset", choices=sorted(gates.PRESETS))
    p.add_argument("--runtime", choices=("auto",) + runtime.names(), default="auto",
                   help="agent runtime to read (default: auto-detect)")
    p.add_argument("--session", help="session id (default: the runtime's session env var)")
    p.add_argument("--transcript", help="explicit transcript path, e.g. a hook's transcript_path")
    p.add_argument("--list-presets", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--arm", action="store_true",
                   help="record this session's role, gate and window (needs --role)")
    g.add_argument("--disarm", action="store_true", help="remove this session's marker")
    g.add_argument("--hook", action="store_true",
                   help="post-tool backstop: read hook JSON on stdin; always exits 0")
    p.add_argument("--label", help='--arm: free text naming the work, e.g. "session 3 of /abs/plan.md"')
    return p


def _die(msg: str, code: int = EXIT_ERROR) -> int:
    print(f"ctxcheck: {msg}", file=sys.stderr)
    return code


def load_marker(session: str | None) -> dict | None:
    """The armed-session marker for `session`, if any. It supplies role, gate
    and window for a plain `ctxcheck`."""
    return markers.load(session)


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        n = int(raw)
    except ValueError:
        raise CtxError(f"{name} must be an integer, not {raw!r}") from None
    if n <= 0:
        raise CtxError(f"{name} must be positive")
    return n


def resolve_window(window: int | None, preset: str | None, marker: dict | None,
                   usage: runtime.Usage, implicit_preset: bool = True) -> tuple[int, str]:
    """Explicit input wins, then the marker, then what the session reports,
    then a preset. A backend that sets `implicit_preset = False` (Pi: its
    models span every vendor, so no default is better than a wrong one) gets
    a preset only when one is named via CTXCHECK_PRESET; otherwise the
    session is unmeasurable."""
    if window:
        return window, "--window"
    env = _env_int("CTXCHECK_WINDOW")
    if env:
        return env, "CTXCHECK_WINDOW"
    if preset:
        return gates.PRESETS[preset][0], f"preset {preset}"
    if marker and marker.get("contextWindow"):
        return int(marker["contextWindow"]), "marker"
    if usage.window:
        return usage.window, usage.window_source or "session"
    name = os.environ.get("CTXCHECK_PRESET")
    if not name and not implicit_preset:
        model = f" for model {usage.model}" if usage.model else ""
        raise Unmeasurable(f"no context window known{model}; pass --window N "
                           "or set CTXCHECK_WINDOW (or --preset / CTXCHECK_PRESET)")
    name = name or gates.DEFAULT_PRESET
    if name not in gates.PRESETS:
        raise CtxError(f"unknown preset {name!r}; one of: {', '.join(gates.PRESETS)}")
    return gates.PRESETS[name][0], f"preset {name}"


def list_presets() -> int:
    width = max(len(k) for k in gates.PRESETS)
    for name, (size, why) in gates.PRESETS.items():
        mark = " (default)" if name == gates.DEFAULT_PRESET else ""
        print(f"  {name:<{width}}  {size:>9,}  {why}{mark}")
    print("\n  default gate per role, derived from the window (--gate overrides):")
    print(f"    {'':19}   {'worker':>7} {'planner':>7} {'coordinator':>11}")
    for floor, worker_pct, coord_pct, why in gates.BANDS:
        print(f"    window >= {floor:>9,}   {worker_pct:>6}% {worker_pct:>6}% "
              f"{coord_pct:>10}%   {why}")
    return EXIT_OK


def measure(a: argparse.Namespace) -> int:
    if a.window is not None and a.window <= 0:
        raise CtxError("--window must be positive")
    if a.gate is not None and a.gate <= 0:
        raise CtxError("--gate must be positive")

    res = None
    try:
        res = runtime.resolve(a.runtime, a.session, a.transcript)
        usage = res.backend.read(res.path)
        session = res.session or res.backend.session_of(res.path)
        marker = load_marker(session)
        window, window_src = resolve_window(
            a.window, a.preset, marker, usage,
            getattr(res.backend, "implicit_preset", True))
    except Unmeasurable as e:
        if a.as_json:
            print(json.dumps({"runtime": res.backend.name if res else None,
                              "measurable": False,
                              "message": str(e), "tried": e.tried}, indent=2))
        tried = "".join(f"\n  tried {t}" for t in e.tried)
        return _die(f"{e}{tried}", EXIT_UNMEASURABLE)

    role = a.role or (marker or {}).get("role")
    explicit = a.gate if a.gate is not None else (marker or {}).get("handoffPct")
    gate, gate_src = gates.resolve_gate(role, window, explicit)
    if marker and a.gate is None and explicit is not None:
        gate_src = "marker"

    ratio = usage.used / window * 100
    pct = round(ratio, 1)
    over = gate is not None and ratio > gate

    out = {
        "runtime": res.backend.name,
        "role": role,
        "used_tokens": usage.used,
        "context_window": window,
        "window_source": window_src,
        "used_percentage": pct,
        "remaining_percentage": round(100 - pct, 1),
        "remaining_tokens": max(window - usage.used, 0),
        "model": usage.model,
        "session_id": session,
        "transcript": str(res.path),
        "measure_source": res.measure_source,
        "stale_seconds": usage.stale_seconds,
        "threshold_percentage": gate,
        "threshold_source": gate_src if gate is not None else None,
        "over_threshold": over if gate is not None else None,
    }

    if a.as_json:
        print(json.dumps(out, indent=2))
    else:
        line = (f"context: {usage.used:,}/{window:,} tokens "
                f"({pct}% used, {window_src}, {res.backend.name})")
        if usage.stale_seconds is not None:
            line += f"\nfrom the status line, {usage.stale_seconds:.0f} s ago"
        if gate is not None:
            line += f"\nthreshold {gate}% ({gate_src}): " + (
                "EXCEEDED - stop at a safe point and hand off" if over else "ok")
        print(line)
    return EXIT_GATE_EXCEEDED if over else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "statusline-tee":
        return tee.main(args[1:])
    if "--hook" in args:  # parses its own args: it must exit 0 even when they are wrong
        return hook.main(args)
    subs = _subcommand_parsers()
    try:
        if args and args[0] in subs:
            sa = subs[args[0]].parse_args(args[1:])
            if args[0] == "install-statusline":
                return install.install_statusline(sa.target_runtime, sa.dry_run, sa.uninstall)
            if args[0] == "install-hook":
                return install.install_hook(sa.target_runtime, sa.dry_run, sa.uninstall)
            return _die(f"{args[0]}: {NOT_IMPLEMENTED}")

        a = _parser().parse_args(args)
        if a.arm:
            return markers.arm(a)
        if a.disarm:
            return markers.disarm(a)
        if a.list_presets:
            return list_presets()
        return measure(a)
    except Unmeasurable as e:  # --arm / --disarm on a runtime with no budget
        tried = "".join(f"\n  tried {t}" for t in e.tried)
        return _die(f"{e}{tried}", EXIT_UNMEASURABLE)
    except CtxError as e:
        return _die(str(e))
