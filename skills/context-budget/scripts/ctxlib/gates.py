"""Window presets and the per-role handoff gates.

What rots a session is the *absolute* context it carries, not the fraction of
its window it has used. A worker on a 1M window handing off at 25% is carrying
250K tokens - a full phase's worth of work. The same 25% on Codex's 258K window
is 65K: it would quit before it finished reading the repo's doctrine files, and
every phase would chew through continuations. So the default gate is derived
from the window the session actually has, and the bands land every session at
roughly 150-250K tokens of accumulated context.

A planner budgets like a worker. `--gate` (or the run-config keys
`coordinator_handoff_pct` / `worker_handoff_pct`, passed as `--gate` by
run-plan) overrides the band for one session; `<state home>/bands.json`
replaces the band table for every session (see load_bands).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import CtxError
from . import paths

COORDINATOR, WORKER, PLANNER = "coordinator", "worker", "planner"
ROLES = (COORDINATOR, WORKER, PLANNER)

# Point-in-time sizes, refreshed 2026-10-15. These rot - vendors move them.
# Treat the menu as a convenience, not a source of truth: --window takes a raw
# number, the Claude backend derives the window from env and model rules, and
# on Codex the live window is read from the session's own token_count events
# (model_context_window), never from this table.
PRESETS: dict[str, tuple[int, str]] = {
    "claude-1m": (1_000_000, "Claude 1M window (Opus >= 4.7, Sonnet >= 5, Haiku 5.5, [1m] variants)"),
    "claude-200k": (200_000, "Claude 200K window (4.6 and older without [1m], or 1M disabled)"),
    "codex-272k-effective": (258_400, "Codex 272K cap x 95% effective; live sessions report their own"),
}
DEFAULT_PRESET = "claude-1m"

Band = tuple[int, float, float, str]

# (floor_window, worker/planner %, coordinator %, what that means) - the first
# floor the window reaches wins, highest first.
BANDS: tuple[Band, ...] = (
    (600_000, 25.0, 25.0, "1M -> hands off near 250K used"),
    (150_000, 75.0, 75.0, "Codex's 258K -> ~194K; Claude's 200K -> 150K"),
    (0, 80.0, 80.0, "smaller windows -> still leaves a real reserve"),
)


def bands_path() -> Path:
    return paths.state_home() / "bands.json"


def _pct(path: Path, i: int, key: str, raw) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 < raw <= 100:
        raise CtxError(f"{path}: band {i}: {key} must be a percentage in (0, 100], not {raw!r}")
    return float(raw)


def load_bands() -> tuple[tuple[Band, ...], str]:
    """(bands, source). bands.json, when present, replaces the built-in table:

        [{"min_window": 600000, "worker": 25, "coordinator": 25},
         {"min_window": 150000, "worker": 75},
         {"min_window": 0, "worker": 80, "note": "what this band is for"}]

    `coordinator` defaults to `worker`; a planner always budgets like a
    worker. One band must have min_window 0 so every window gets a gate. The
    gate is fixed into a session's marker when it arms, so an edit applies
    from the next --arm on."""
    path = bands_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return BANDS, "built-in"
    except OSError as e:
        raise CtxError(f"{path}: {e.strerror or e}") from None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise CtxError(f"{path}: not valid JSON ({e})") from None
    if not isinstance(data, list) or not data:
        raise CtxError(f"{path}: must be a non-empty JSON list of bands")
    out: list[Band] = []
    for i, band in enumerate(data):
        if not isinstance(band, dict):
            raise CtxError(f"{path}: band {i} must be an object")
        floor = band.get("min_window")
        if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
            raise CtxError(f"{path}: band {i}: min_window must be a whole number >= 0")
        worker = _pct(path, i, "worker", band.get("worker"))
        coord = _pct(path, i, "coordinator", band.get("coordinator", worker))
        out.append((floor, worker, coord, str(band.get("note", ""))))
    out.sort(key=lambda b: b[0], reverse=True)
    if out[-1][0] != 0:
        raise CtxError(f"{path}: one band needs min_window 0 so every window gets a gate")
    return tuple(out), str(path)


def band_for(role: str, window: int) -> float:
    """The default handoff gate for `role` on a window of this size."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    for floor, worker_pct, coord_pct, _ in load_bands()[0]:
        if window >= floor:
            return coord_pct if role == COORDINATOR else worker_pct
    raise AssertionError("bands must end with a 0 floor")


def resolve_gate(role: str | None, window: int,
                 explicit: float | None) -> tuple[float | None, str]:
    """(gate, source). An explicit gate wins; else a role derives one from the
    window; with neither, there is no threshold and usage is only reported."""
    if explicit is not None:
        return float(explicit), "--gate"
    if role:
        source = load_bands()[1]
        where = "" if source == "built-in" else " from bands.json"
        return band_for(role, window), f"{role} band for a {window:,} window{where}"
    return None, "unset"
