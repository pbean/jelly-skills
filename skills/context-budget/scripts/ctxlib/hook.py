"""`ctxcheck --hook [--runtime R]`: the post-tool backstop.

The agent runtime runs this after a tool call with its hook JSON on stdin. It
stays silent unless *this* session is armed (has a marker) and is over its
gate; then it prints the runtime's "add context for the model" shape with a
role-specific instruction. It re-nags only after usage climbs RENAG_POINTS
more percentage points, so one overage is not repeated on every tool call.

Never fails loudly: a hook that errors on every tool call is worse than a hook
that misses a warning, so every unexpected condition exits 0 with no output.

Runtimes are entries in RUNTIMES (input normalization + output shape); later
versions add theirs there. Without --runtime the entry is inferred from the
payload's keys.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable, NamedTuple, Optional, Sequence

from . import EXIT_OK
from . import gates, markers, runtime

RENAG_POINTS = 5


class Event(NamedTuple):
    session: Optional[str]
    transcript: Optional[str]


class HookRuntime(NamedTuple):
    backend: str                       # the ctxlib backend that reads its sessions
    parse: Callable[[dict], Event]     # payload -> session id, transcript path
    emit: Callable[[str], str]         # message -> stdout text
    sniff: Callable[[dict], bool]      # does this payload look like ours?


def _str(v: object) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def _snake(payload: dict) -> Event:
    return Event(_str(payload.get("session_id")), _str(payload.get("transcript_path")))


def _post_tool_use(msg: str) -> str:
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}})


def _is_codex(payload: dict) -> bool:
    # verified 2026-10-15: Codex 0.162.1 PostToolUse stdin carries turn_id and
    # a rollout-*.jsonl transcript_path (learn.chatgpt.com/docs/hooks;
    # openai/codex rust-v0.162.1 codex-rs/hooks/src/events/post_tool_use.rs).
    path = _str(payload.get("transcript_path")) or ""
    return "turn_id" in payload or Path(path).name.startswith("rollout-")


RUNTIMES: dict[str, HookRuntime] = {
    # verified 2026-10-15 (Codex first: its payload is a superset of Claude's):
    # Codex: https://learn.chatgpt.com/docs/hooks - stdin session_id,
    # transcript_path, cwd, hook_event_name, model, turn_id, tool_name ("Bash"
    # for shell commands), tool_input, tool_response; PostToolUse output
    # hookSpecificOutput.additionalContext, ~2.5K-token cap per output.
    "codex": HookRuntime("codex", _snake, _post_tool_use, _is_codex),
    # Claude: https://code.claude.com/docs/en/hooks - stdin session_id,
    # transcript_path, cwd, hook_event_name, tool_name, tool_input,
    # tool_response, tool_use_id; additionalContext capped at 10,000 chars.
    # Live-checked with Claude Code 2.1.301.
    "claude": HookRuntime("claude", _snake, _post_tool_use,
                          lambda p: bool(_str(p.get("session_id")))),
}


def _infer(payload: dict) -> Optional[str]:
    for name, rt in RUNTIMES.items():
        if rt.sniff(payload):
            return name
    return None


def message(state: dict, used: int, window: int, pct: float, gate: float) -> str:
    label = state.get("label")
    which = f" for {label}" if label else ""
    head = (f"CONTEXT BUDGET EXCEEDED{which}: {used:,}/{window:,} tokens ({pct}%) "
            f"against a {gate}% handoff threshold.")
    role = state.get("role")
    if role == gates.COORDINATOR:
        return (f"{head} Finish the phase in flight, then hand off to a fresh "
                "coordinator per the run-plan skill's references/coordinator-handoff.md "
                "before starting another phase. If only one phase remains, note the "
                "overage and finish it instead.")
    if role == gates.PLANNER:
        return (f"{head} At the next section boundary, save the draft plan and a "
                "planning continuation and stop, per the context-budget skill's "
                "references/planner.md. Do not start exploring a new area first.")
    # A worker may never have loaded the skill - it has only its phase prompt -
    # so this carries the procedure itself, not just a pointer to it.
    return (f"{head} Stop at the next safe point and hand the rest of this phase to a "
            "fresh worker (details: the context-budget skill's references/worker.md). "
            "A safe point is between numbered steps with the tree coherent - never "
            "mid-edit, and never inside an ablation check with a source file still "
            "reverted or a backup still on disk. Leave your edits uncommitted in the "
            "worktree (your successor works in the same one), save a patch backup, "
            "write the continuation note your phase file names, disarm with "
            "`ctxcheck --disarm`, then report completion as your phase file says, with "
            "'PHASE-STATUS: continuation-required' as the first line of the body and "
            "outcome succeeded, not failed. Do not start the next numbered step. If "
            "the only work left is the commit itself, make it and finish the phase "
            "normally instead.")


def _transcript(backend, event: Event, session: str) -> Optional[Path]:
    """Prefer the payload's transcript_path; else look the session up."""
    if event.transcript:
        path = Path(event.transcript).expanduser()
        if path.is_file():
            return path
    return backend.find(session)


def _run(argv: Sequence[str]) -> int:
    requested = None
    if "--runtime" in argv:
        i = list(argv).index("--runtime")
        requested = argv[i + 1] if i + 1 < len(argv) else None
    raw = sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return EXIT_OK
    name = requested if requested in RUNTIMES else (None if requested else _infer(payload))
    if not name:
        return EXIT_OK
    rt = RUNTIMES[name]
    event = rt.parse(payload)
    if not event.session and not event.transcript:
        return EXIT_OK

    state = markers.load(event.session)
    backend = runtime.by_name(rt.backend)
    if state is None and event.transcript:
        # The hook's session id and the id arming recorded can differ (Codex
        # names a thread by its rollout file); try the transcript's own id.
        alt = backend.session_of(Path(event.transcript))
        state = markers.load(alt) if alt != event.session else None
    if state is None:
        return EXIT_OK  # not armed: the common case
    session = state.get("sessionId") or event.session
    if state.get("runtime") and state["runtime"] != backend.name:
        backend = runtime.by_name(state["runtime"])

    path = _transcript(backend, event, session)
    if not path:
        return EXIT_OK
    usage = backend.read(path)
    window = int(state.get("contextWindow") or usage.window or 0)
    if window <= 0:
        return EXIT_OK
    gate = state.get("handoffPct")
    if gate is None:
        if state.get("role") not in gates.ROLES:
            return EXIT_OK
        gate = gates.band_for(state["role"], window)
    gate = float(gate)
    pct = round(usage.used / window * 100, 1)
    if pct <= gate:
        return EXIT_OK
    warned = state.get("warnedAtPct")
    if warned is not None and pct < float(warned) + RENAG_POINTS:
        return EXIT_OK
    state["warnedAtPct"] = pct
    try:
        markers.write(state)
    except OSError:
        pass  # still warn; at worst the next call warns again
    sys.stdout.write(rt.emit(message(state, usage.used, window, pct, gate)) + "\n")
    return EXIT_OK


def main(argv: Sequence[str]) -> int:
    try:
        return _run(argv)
    except BaseException:  # noqa: BLE001 - a hook must never fail loudly
        return EXIT_OK
