"""Window presets and the per-role handoff gates.

What rots a session is the *absolute* context it carries, not the fraction of
its window it has used. A worker on a 1M window handing off at 28% is carrying
280K tokens - a full phase's worth of work. The same 28% on Codex's 258K window
is 72K: it would quit before it finished reading the repo's doctrine files, and
every phase would chew through continuations. So the default gate is derived
from the window the session actually has, and the bands land every session at
roughly 150-280K tokens of accumulated context.

A coordinator's band sits a little lower on large windows: it carries the whole
run, and handing off a few points early is cheap next to a degraded
coordinator. A planner budgets like a worker. `--gate` (or the run-config keys
`coordinator_handoff_pct` / `worker_handoff_pct`, passed as `--gate` by
run-plan) overrides the band.
"""

from __future__ import annotations

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

# (floor_window, worker/planner %, coordinator %, what that means) - the first
# floor the window reaches wins, highest first.
BANDS: tuple[tuple[int, float, float, str], ...] = (
    (600_000, 28.0, 26.0, "1M -> hands off near 260-280K used"),
    (150_000, 75.0, 75.0, "Codex's 258K -> ~194K; Claude's 200K -> 150K"),
    (0, 80.0, 80.0, "smaller windows -> still leaves a real reserve"),
)


def band_for(role: str, window: int) -> float:
    """The default handoff gate for `role` on a window of this size."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    for floor, worker_pct, coord_pct, _ in BANDS:
        if window >= floor:
            return coord_pct if role == COORDINATOR else worker_pct
    raise AssertionError("BANDS must end with a 0 floor")


def resolve_gate(role: str | None, window: int,
                 explicit: float | None) -> tuple[float | None, str]:
    """(gate, source). An explicit gate wins; else a role derives one from the
    window; with neither, there is no threshold and usage is only reported."""
    if explicit is not None:
        return float(explicit), "--gate"
    if role:
        return band_for(role, window), f"{role} band for a {window:,} window"
    return None, "unset"
