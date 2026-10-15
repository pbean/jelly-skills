"""Arming: a marker file says "this session measures itself, at this gate".

    markers/<session>.json = {sessionId, runtime, role, label, contextWindow,
                              handoffPct, warnedAtPct, armedAt}

`ctxcheck --arm --role R --label "..." [--gate PCT] [--window N]` writes it;
`ctxcheck --disarm` removes it. While it exists, a plain `ctxcheck` takes its
role, gate and window from it, and the post-tool hook (`--hook`) warns once the
session goes over the gate. The gate is derived from the window for the role
unless --gate names one; the window is the session's own, as measured at arm
time, unless --window / CTXCHECK_WINDOW / --preset overrides it.

Markers outlive crashed sessions, so arming reaps any older than STALE_DAYS.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from . import EXIT_OK, CtxError
from . import gates, paths, runtime

STALE_DAYS = 7


def load(session: str | None) -> dict | None:
    """The marker for `session`, or None (not armed, or unreadable)."""
    if not session:
        return None
    try:
        data = json.loads(paths.marker_path(session).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write(state: dict) -> Path:
    """Write a marker atomically (temp file plus rename) and return its path."""
    path = paths.marker_path(state["sessionId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def reap_stale(keep: Path | None = None) -> int:
    """Delete markers untouched for STALE_DAYS, never `keep`. Best-effort."""
    cutoff = time.time() - STALE_DAYS * 86400
    reaped = 0
    try:
        found = list(paths.markers_dir().glob("*.json"))
    except OSError:
        return 0
    for m in found:
        if keep is not None and m == keep:
            continue
        try:
            if m.stat().st_mtime < cutoff:
                m.unlink()
                reaped += 1
        except OSError:
            continue
    return reaped


def arm(a) -> int:
    """`--arm`: measure the session once, then record its role, gate and window."""
    from .cli import resolve_window  # cli imports this module

    if not a.role:
        raise CtxError("--arm needs --role coordinator|worker|planner")
    if a.window is not None and a.window <= 0:
        raise CtxError("--window must be positive")
    if a.gate is not None and a.gate <= 0:
        raise CtxError("--gate must be positive")
    # Resolving reads the session's own data, which both proves which runtime
    # this is (env vars leak into child agents) and discovers its window.
    res = runtime.resolve(a.runtime, a.session, a.transcript)
    session = res.session or res.backend.session_of(res.path)
    if not session:
        raise CtxError("--arm needs a session id; run it from inside the session it arms")
    usage = res.backend.read(res.path)
    window, window_src = resolve_window(a.window, a.preset, None, usage,
                                        getattr(res.backend, "implicit_preset", True))
    gate, gate_src = gates.resolve_gate(a.role, window, a.gate)
    path = write({
        "sessionId": session,
        "runtime": res.backend.name,
        "role": a.role,
        "label": a.label,
        "contextWindow": window,
        "handoffPct": gate,
        "warnedAtPct": None,
        "armedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    reaped = reap_stale(keep=path)
    pct = round(usage.used / window * 100, 1)
    print(f"armed: {path}\n  role {a.role}  runtime {res.backend.name}  "
          f"window {window:,} ({window_src})  handoff at {gate}% ({gate_src})  "
          f"now {pct}%")
    if a.label:
        print(f"  label: {a.label}")
    if reaped:
        print(f"  reaped {reaped} marker(s) older than {STALE_DAYS} days")
    return EXIT_OK


def _session_for_disarm(a) -> str | None:
    """The session to disarm. Prefer the one detection resolves; if its data
    can't be read any more, fall back to a session id from the env that has a
    marker, so a session can always disarm itself."""
    if a.session:
        return a.session
    try:
        res = runtime.resolve(a.runtime, None, a.transcript)
        sid = res.session or res.backend.session_of(res.path)
        if sid:
            return sid
    except CtxError:
        pass
    backends = (runtime.candidates() if a.runtime == "auto"
                else [runtime.by_name(a.runtime)])
    for b in backends:
        sid = b.session_id()
        if sid and paths.marker_path(sid).exists():
            return sid
    return None


def disarm(a) -> int:
    session = _session_for_disarm(a)
    if not session:
        print("not armed; nothing to do (no session id found)")
        return EXIT_OK
    marker = paths.marker_path(session)
    try:
        marker.unlink()
    except FileNotFoundError:
        print("not armed; nothing to do")
        return EXIT_OK
    except OSError as e:
        raise CtxError(f"cannot remove {marker}: {e}") from e
    print(f"disarmed: {marker}")
    return EXIT_OK

