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
payload's keys. `--event E` names the hook event for runtimes whose payload
doesn't (Antigravity), so the installed command says which event it serves.

OpenCode and Pi have no shell hooks: ctxcheck's own plugin / extension
(scripts/assets/) pipes {runtime, session_id[, used, window]} here and appends
the "message" of our {"message": ...} reply to the tool result. When the
payload carries usage (Pi's ctx.getContextUsage()), it is trusted as is and
no session file is read.
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
    name: Optional[str] = None         # the hook event, when known
    used: Optional[int] = None         # usage the runtime measured itself (Pi)
    window: Optional[int] = None


def _always(event: Event) -> bool:
    return True


class HookRuntime(NamedTuple):
    backend: str                       # the ctxlib backend that reads its sessions
    parse: Callable[[dict], Event]     # payload -> session id, transcript path
    emit: Callable[[str], str]         # message -> stdout text
    sniff: Callable[[dict], bool]      # does this payload look like ours?
    # May this event carry a warning? Others get only `silent`.
    fires: Callable[[Event], bool] = _always
    silent: str = ""                   # stdout when there is nothing to say


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


# --- Cursor, Copilot, Antigravity: measured from the status-line sidecar, so
# a payload's transcript path (the runtime's own transcript) is never read;
# the sidecar is found by session id. Their hook ids are the same ids their
# status lines and env vars carry (Cursor: documented; Copilot, Antigravity:
# unconfirmed live). Each prints "{}" when silent: the contracts say stdout is
# parsed as JSON on exit 0.

def _is_cursor(payload: dict) -> bool:
    return bool(_str(payload.get("conversation_id"))) and (
        "cursor_version" in payload or "generation_id" in payload
        or "workspace_roots" in payload)


def _cursor(payload: dict) -> Event:
    # sessionStart documents session_id as "same as conversation_id".
    return Event(_str(payload.get("conversation_id")) or _str(payload.get("session_id")),
                 None, _str(payload.get("hook_event_name")))


def _is_copilot(payload: dict) -> bool:
    if _str(payload.get("sessionId")):  # camelCase event key (postToolUse)
        return "toolName" in payload or "toolResult" in payload
    # PascalCase event key (PostToolUse): snake_case, VS Code style. Claude's
    # payload has transcript_path and tool_response; Copilot's has neither.
    return (bool(_str(payload.get("session_id"))) and "tool_result" in payload
            and "transcript_path" not in payload)


def _copilot(payload: dict) -> Event:
    return Event(_str(payload.get("sessionId")) or _str(payload.get("session_id")),
                 None, _str(payload.get("hook_event_name")))


def _is_antigravity(payload: dict) -> bool:
    return bool(_str(payload.get("conversationId"))) and (
        "workspacePaths" in payload or "artifactDirectoryPath" in payload
        or "invocationNum" in payload or "stepIdx" in payload)


def _antigravity(payload: dict) -> Event:
    return Event(_str(payload.get("conversationId")), None,
                 _str(payload.get("hookEventName")))


ANTIGRAVITY_EVENT = "PreInvocation"


# --- OpenCode, Pi: our own plugin / extension is the caller, so the payload is
# ctxcheck's: {"runtime": R, "session_id": id, "used"?: n, "window"?: n}.

def _count(v: object) -> Optional[int]:
    # bool is an int subclass; a JSON true is not a token count.
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        return None
    return int(v)


def _ours(runtime_name: str) -> Callable[[dict], bool]:
    return lambda payload: payload.get("runtime") == runtime_name


def _piped(payload: dict) -> Event:
    window = _count(payload.get("window"))
    return Event(_str(payload.get("session_id")), None, None,
                 _count(payload.get("used")), window or None)


def _message(msg: str) -> str:
    return json.dumps({"message": msg})


RUNTIMES: dict[str, HookRuntime] = {
    # OpenCode and Pi first: their payloads are ctxcheck's own and name the
    # runtime, and they carry a session_id that Claude's sniff would take.
    # OpenCode v1/v2: assets/opencode-plugin.ts sends {runtime, session_id};
    # usage is read from opencode.db by session id (OpenCode v1 exposes no
    # session id to tools, so the plugin is the only source of it).
    "opencode": HookRuntime("opencode", _piped, _message, _ours("opencode"),
                            silent="{}"),
    # Pi: assets/pi-extension.ts sends {runtime, session_id, used, window}
    # from ctx.getContextUsage(); without "used" (Pi reports null tokens right
    # after a compaction) the session JSONL is read instead.
    "pi": HookRuntime("pi", _piped, _message, _ours("pi"), silent="{}"),
    # verified 2026-10-15 (Codex first: its payload is a superset of Claude's):
    # Codex: https://learn.chatgpt.com/docs/hooks - stdin session_id,
    # transcript_path, cwd, hook_event_name, model, turn_id, tool_name ("Bash"
    # for shell commands), tool_input, tool_response; PostToolUse output
    # hookSpecificOutput.additionalContext, ~2.5K-token cap per output.
    "codex": HookRuntime("codex", _snake, _post_tool_use, _is_codex),
    # Cursor: https://cursor.com/docs/hooks.md - postToolUse stdin carries
    # conversation_id, generation_id, model, hook_event_name, cursor_version,
    # workspace_roots, user_email, transcript_path, tool_name, tool_input,
    # tool_output, tool_use_id, cwd, duration; stdout
    # {"additional_context": "..."} is "injected into the conversation after
    # the tool result" (no documented length cap). LIVE-UNVERIFIED.
    "cursor": HookRuntime("cursor", _cursor,
                          lambda msg: json.dumps({"additional_context": msg}),
                          _is_cursor, silent="{}"),
    # Copilot CLI 1.0.98: https://docs.github.com/en/copilot/reference/
    # hooks-configuration - the event key's casing picks the payload casing:
    # postToolUse -> {sessionId, timestamp, cwd, toolName, toolArgs,
    # toolResult}; PostToolUse -> {hook_event_name, session_id, timestamp,
    # cwd, tool_name, tool_input, tool_result}. Output: top-level
    # {"additionalContext": "..."}, appended to the tool result, 10 KB cap.
    # LIVE-UNVERIFIED.
    "copilot": HookRuntime("copilot", _copilot,
                           lambda msg: json.dumps({"additionalContext": msg}),
                           _is_copilot, silent="{}"),
    # Antigravity agy 1.3.2: ~/.gemini/antigravity-cli/builtin/skills/
    # agy-customizations/docs/hooks.md - camelCase payloads with common
    # conversationId, workspacePaths, transcriptPath, artifactDirectoryPath,
    # modelName; PostToolUse "Expects an empty JSON object {}", so the
    # warning rides PreInvocation (before each model call) as
    # {"injectSteps": [{"ephemeralMessage": "..."}]} ("transient system
    # message"). Pre- and PostInvocation payloads are identical, so the
    # installed command passes --event PreInvocation; any other event, or
    # none, gets {}.
    "antigravity": HookRuntime(
        "antigravity", _antigravity,
        lambda msg: json.dumps({"injectSteps": [{"ephemeralMessage": msg}]}),
        _is_antigravity, fires=lambda e: e.name == ANTIGRAVITY_EVENT, silent="{}"),
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


def _flag(argv: Sequence[str], flag: str) -> Optional[str]:
    if flag not in argv:
        return None
    i = list(argv).index(flag)
    return argv[i + 1] if i + 1 < len(argv) else None


def _run(argv: Sequence[str], out: dict) -> Optional[str]:
    """The warning to print, or None. Sets out["silent"] once the runtime is known."""
    requested = _flag(argv, "--runtime")
    if requested in RUNTIMES:
        out["silent"] = RUNTIMES[requested].silent
    raw = sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        return None
    name = requested if requested in RUNTIMES else (None if requested else _infer(payload))
    if not name:
        return None
    rt = RUNTIMES[name]
    out["silent"] = rt.silent
    event = rt.parse(payload)
    named = _flag(argv, "--event")
    if named:
        event = event._replace(name=named)
    if not rt.fires(event):
        return None
    if not event.session and not event.transcript:
        return None

    state = markers.load(event.session)
    backend = runtime.by_name(rt.backend)
    if state is None and event.transcript:
        # The hook's session id and the id arming recorded can differ (Codex
        # names a thread by its rollout file); try the transcript's own id.
        alt = backend.session_of(Path(event.transcript))
        state = markers.load(alt) if alt != event.session else None
    if state is None:
        return None  # not armed: the common case
    session = state.get("sessionId") or event.session
    if state.get("runtime") and state["runtime"] != backend.name:
        backend = runtime.by_name(state["runtime"])

    if event.used is not None:
        # The runtime measured it (Pi): trust it over its session file.
        used, measured_window = event.used, event.window
    else:
        path = _transcript(backend, event, session)
        if not path:
            return None
        usage = backend.read(path)
        used, measured_window = usage.used, usage.window
    window = int(state.get("contextWindow") or measured_window or 0)
    if window <= 0:
        return None
    gate = state.get("handoffPct")
    if gate is None:
        if state.get("role") not in gates.ROLES:
            return None
        gate = gates.band_for(state["role"], window)
    gate = float(gate)
    pct = round(used / window * 100, 1)
    if pct <= gate:
        return None
    warned = state.get("warnedAtPct")
    if warned is not None and pct < float(warned) + RENAG_POINTS:
        return None
    state["warnedAtPct"] = pct
    try:
        markers.write(state)
    except OSError:
        pass  # still warn; at worst the next call warns again
    return rt.emit(message(state, used, window, pct, gate))


def main(argv: Sequence[str]) -> int:
    out = {"silent": ""}
    try:
        text = _run(argv, out)
    except BaseException:  # noqa: BLE001 - a hook must never fail loudly
        text = None
    text = text if text is not None else out["silent"]
    if text:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except BaseException:  # noqa: BLE001
            pass
    return EXIT_OK
