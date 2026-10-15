"""Copilot CLI, Antigravity (`agy`) and Cursor CLI: read the status-line sidecar.

These runtimes keep the live context count in memory only, but each passes it
to a user-configured status-line command as JSON on stdin. `ctxcheck
install-statusline R` wraps that command in `ctxcheck statusline-tee`, which
snapshots the payload to

    <state>/statusline/<runtime>/<session>.json   {received_at, cwd, runtime, session, payload}

and this module reads the snapshot back. It only updates while the
interactive TUI renders, so every read reports its age (`stale_seconds`) and
anything older than STALE_AFTER seconds is refused as unmeasurable.

Payload fields used (verified 2026-10-15; the readers accept snake_case and
camelCase spellings because Copilot's and Cursor's are not documented):

  Antigravity 1.3.2 - https://www.antigravity.google/docs/cli/statusline
    CONFIRMED: conversation_id (session_id is a documented alias), cwd,
    model.{id, display_name}, workspace.current_dir,
    context_window.{context_window_size, used_percentage,
    current_usage.{input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens}}.
    INFERRED (binary strings only): the ANTIGRAVITY_CONVERSATION_ID env var
    for tool subprocesses. Fallback: ~/.gemini/antigravity-cli/cache/
    last_conversations.json, a flat {"<abs workspace>": "<conversation id>"}.
  Cursor CLI - no official status-line docs; COMMUNITY (copies of Cursor's
    built-in statusline skill, 2026-04; forum, 2026-07): session_id, cwd,
    model.{id, display_name}, workspace.current_dir,
    context_window.{context_window_size, used_percentage, current_usage}.
    current_usage subfields are UNKNOWN (null in every published example), so
    used falls back to used_percentage x context_window_size. No session env
    var: CURSOR_AGENT=1 (COMMUNITY) only says Cursor is running, and the
    newest sidecar for the cwd is used.
  Copilot CLI 1.0.98 (npm @github/copilot, 2026-10-14) - the docs
    (docs.github.com/en/copilot/reference/copilot-cli-reference/
    cli-config-dir-reference) say only that the command "receives session
    JSON on stdin"; keys are FROM-BUNDLE (cli-main.js of
    @github/copilot-linux-x64@1.0.98, the status-line payload builder):
    session_id (null until a session exists), cwd, model.{id, display_name},
    workspace.current_dir, context_window.{context_window_size,
    used_percentage, current_context_tokens, displayed_context_limit,
    current_context_used_percentage, current_usage.{input_tokens,
    output_tokens, cache_creation_input_tokens, cache_read_input_tokens}}.
    The numbers are computed in native code; per the changelog they "match
    the model badge and /context output". Preferring current_context_tokens
    and displayed_context_limit is our reading, UNCONFIRMED live.
    CONFIRMED (changelog 1.0.29): tool subprocesses get
    COPILOT_AGENT_SESSION_ID.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .. import Unmeasurable
from ..paths import statusline_dir
from ..runtime import Usage
from . import as_int, newest

# A snapshot older than this is not a measurement of the current context.
STALE_AFTER = 900

# Keys naming the session in a payload, in preference order.
SESSION_KEYS = ("session_id", "conversation_id", "sessionId", "conversationId")

def _get(d: object, *keys: str) -> object:
    """The first present, non-null value among `keys` of a dict."""
    if isinstance(d, dict):
        for k in keys:
            v = d.get(k)
            if v is not None:
                return v
    return None


def payload_session(payload: object) -> str | None:
    sid = _get(payload, *SESSION_KEYS)
    if sid is None:
        sid = _get(_get(payload, "session"), "id")
    return str(sid) if isinstance(sid, (str, int)) and str(sid) else None


def payload_cwd(payload: object) -> str | None:
    cwd = _get(payload, "cwd")
    if not isinstance(cwd, str):
        cwd = _get(_get(payload, "workspace"), "current_dir", "currentDir", "cwd")
    return cwd if isinstance(cwd, str) and cwd else None


def safe_name(session: str) -> str:
    """A session id as a file name: ids are UUID-like, but the payload is
    untrusted input, so anything else is reduced to a safe spelling."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", session)[:200].lstrip(".")
    return name or "_"


def extract(payload: object) -> tuple[int | None, int | None, str | None]:
    """(used tokens, window size, model) from a status-line payload.

    used = Copilot's current_context_tokens; else current_usage input +
    cache_creation_input + cache_read_input (the tokens in the context for
    the last call, as Claude Code's status line defines them); else
    used_percentage x window. The window is Copilot's displayed_context_limit
    (what its badge divides by; the smaller, earlier-gating choice) else
    context_window_size. Either part is None when the payload lacks it.
    Totals (total_input_tokens...) are cumulative over the session and are
    never used.
    """
    cw = _get(payload, "context_window", "contextWindow")
    size = as_int(_get(cw, "displayed_context_limit", "displayedContextLimit",
                       "context_window_size", "contextWindowSize")) or None
    used = None
    current = _get(cw, "current_context_tokens", "currentContextTokens")
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        used = int(current)
    cur = _get(cw, "current_usage", "currentUsage")
    if used is None and isinstance(cur, dict):
        fields = (("input_tokens", "inputTokens"),
                  ("cache_creation_input_tokens", "cacheCreationInputTokens"),
                  ("cache_read_input_tokens", "cacheReadInputTokens"))
        present = [_get(cur, *f) for f in fields]
        if any(v is not None for v in present):
            used = sum(as_int(v) for v in present)
    if used is None:
        pct = _get(cw, "used_percentage", "usedPercentage")
        if isinstance(pct, (int, float)) and not isinstance(pct, bool) and size:
            used = int(round(float(pct) * size / 100))
    model = _get(payload, "model")
    if isinstance(model, dict):
        model = _get(model, "id", "display_name", "displayName", "name")
    return used, size, model if isinstance(model, str) else None


def load(path: Path) -> dict:
    try:
        snap = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Unmeasurable(f"cannot read status-line snapshot {path}: {e}") from e
    if not isinstance(snap, dict):
        raise Unmeasurable(f"status-line snapshot {path} is not an object")
    return snap


def _same_dir(a: str, b: Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(str(b))
    except (OSError, ValueError):
        return False


class StatuslineBackend:
    measure_source = "statusline"
    # The window comes from the payload; with none there, any preset would be
    # a guess about a third-party model.
    implicit_preset = False

    def __init__(self, name: str, session_env: str | None, present_env: str | None = None,
                 present_value: str | None = None) -> None:
        self.name = name
        self._session_env = session_env
        # For messages: what was tried. Cursor has no session variable.
        self.session_env = session_env or present_env or "(none)"
        self._present_env = present_env
        self._present_value = present_value

    def __repr__(self) -> str:
        return f"<StatuslineBackend {self.name}>"

    def present(self) -> bool:
        if not self._present_env:
            return False
        val = os.environ.get(self._present_env)
        return bool(val) and (self._present_value is None or val == self._present_value)

    def session_id(self) -> str | None:
        if not self._session_env:
            return None
        return os.environ.get(self._session_env) or None

    def directory(self) -> Path:
        return statusline_dir(self.name)

    def find(self, session: str) -> Path | None:
        p = self.directory() / f"{safe_name(session)}.json"
        return p if p.is_file() else None

    def fallback(self, cwd: Path) -> Path | None:
        """The newest snapshot whose recorded cwd is `cwd`."""
        matches = []
        for p in self.directory().glob("*.json"):
            try:
                snap = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(snap, dict) and isinstance(snap.get("cwd"), str) \
                    and _same_dir(snap["cwd"], cwd):
                matches.append(p)
        return newest(matches)

    def read(self, path: Path) -> Usage:
        snap = load(path)
        received = snap.get("received_at")
        if not isinstance(received, (int, float)) or isinstance(received, bool):
            raise Unmeasurable(f"status-line snapshot {path} has no received_at")
        age = max(time.time() - float(received), 0.0)
        if age > STALE_AFTER:
            raise Unmeasurable(
                f"{self.name} status line hasn't refreshed in {int(age)} s "
                f"(limit {STALE_AFTER} s); it only updates while the TUI is on screen. "
                f"Snapshot: {path}")
        used, size, model = extract(snap.get("payload"))
        if used is None:
            raise Unmeasurable(f"{self.name} status-line payload carries no token usage "
                               f"yet (snapshot {path})")
        return Usage(used, size, model, "status line" if size else None, round(age, 1))

    def session_of(self, path: Path) -> str:
        try:
            sid = load(path).get("session")
        except Unmeasurable:
            sid = None
        return sid if isinstance(sid, str) and sid else path.stem


class AntigravityBackend(StatuslineBackend):
    def fallback(self, cwd: Path) -> Path | None:
        """last_conversations.json maps a workspace to its latest
        conversation; fall back to the newest snapshot for the cwd."""
        index = Path.home() / ".gemini" / "antigravity-cli" / "cache" / "last_conversations.json"
        try:
            table = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = None
        if isinstance(table, dict):
            for workspace, conv in table.items():
                if isinstance(conv, str) and conv and _same_dir(workspace, cwd):
                    found = self.find(conv)
                    if found:
                        return found
        return super().fallback(cwd)


COPILOT = StatuslineBackend("copilot", "COPILOT_AGENT_SESSION_ID")
ANTIGRAVITY = AntigravityBackend("antigravity", "ANTIGRAVITY_CONVERSATION_ID")
CURSOR = StatuslineBackend("cursor", None, "CURSOR_AGENT", "1")

# Detection order after Pi: Copilot, Antigravity, Cursor.
BACKENDS = [COPILOT, ANTIGRAVITY, CURSOR]
