"""Where ctxcheck keeps mutable state.

    ${CTXCHECK_HOME:-${XDG_STATE_HOME:-~/.local/state}/jelly-skills/ctxcheck}
        markers/<session>.json                 armed sessions
        statusline/<runtime>/<session>.json    status-line sidecar snapshots

State never lives next to the code: `npx skills` installs copies that are
replaced on every reinstall.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "jelly-skills"


def state_home() -> Path:
    env = os.environ.get("CTXCHECK_HOME")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / APP / "ctxcheck"


def markers_dir() -> Path:
    return state_home() / "markers"


def marker_path(session: str) -> Path:
    return markers_dir() / f"{session}.json"


def statusline_dir(runtime: str | None = None) -> Path:
    d = state_home() / "statusline"
    return d / runtime if runtime else d
