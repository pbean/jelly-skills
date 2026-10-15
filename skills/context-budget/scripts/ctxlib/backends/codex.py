"""Codex CLI: CODEX_THREAD_ID -> $CODEX_HOME/sessions/YYYY/MM/DD/
                                  rollout-<YYYY-MM-DDTHH-MM-SS>-<thread>.jsonl

Codex writes an `event_msg` / `token_count` event after every model request:

  * used = info.last_token_usage.total_tokens - the latest active context
    size, the same quantity the TUI's "% context left" is computed from. (The
    old ctxcheck used input_tokens, which drifts from the TUI.)
    `total_token_usage` is cumulative across the session, not context, and
    must never be used here.
  * window = info.model_context_window, as written: Codex stores the
    *effective* window, already x0.95 by default (272,000 -> 258,400).
  * The TUI additionally subtracts a 12,000-token BASELINE from both used and
    window before taking the percentage. We do not; our used_percentage is the
    plain ratio, so it reads a little higher than the TUI's.

verified 2026-10-15: https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/
  protocol/src/protocol.rs (TokenUsage L2270, TokenUsageInfo L2305,
  BASELINE_TOKENS L2448, tokens_in_context_window L2468),
  tui/src/chatwidget.rs:1133 (TUI percent from last_token_usage),
  protocol/src/openai_models.rs:391,519 (effective window percent 95),
  rollout/src/rollout_file_name.rs (filename; reverted threads append
  _<rollout_id>), protocol/src/shell_environment.rs:7 (CODEX_THREAD_ID is set
  for shell tool subprocesses; CODEX_SESSION_ID carries the root session).

Subagent threads get their own rollout file, so there is no sidechain filter.
Old rollouts may be zstd-compressed (.jsonl.zst); those are not read.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..runtime import Usage
from . import as_int, iter_jsonl, newest

NAME = "codex"
SESSION_ENV = "CODEX_THREAD_ID"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _first_record(path: Path) -> dict | None:
    for rec in iter_jsonl(path):
        return rec
    return None


class CodexBackend:
    name = NAME
    session_env = SESSION_ENV

    def session_id(self) -> str | None:
        return os.environ.get(SESSION_ENV) or None

    def sessions_root(self) -> Path:
        return codex_home() / "sessions"

    def find(self, session: str) -> Path | None:
        """Rollouts are foldered by date, so glob on the id rather than
        reconstructing the timestamp. `*` after the id admits the
        `_<rollout_id>` suffix of a reverted thread."""
        root = self.sessions_root()
        hits = list(root.glob(f"**/rollout-*-{session}.jsonl"))
        hits += root.glob(f"**/rollout-*-{session}_*.jsonl")
        return newest(hits)

    def fallback(self, cwd: Path) -> Path | None:
        """Newest root-thread rollout started in `cwd`, for when the thread id
        is unknown. session_meta is the first record, so only heads are read."""
        want = str(cwd)
        root = self.sessions_root()
        stamped = []
        for p in root.glob("**/rollout-*.jsonl"):
            try:
                stamped.append((p.stat().st_mtime, p))
            except OSError:
                continue
        stamped.sort(key=lambda t: t[0], reverse=True)
        for _, p in stamped:
            try:
                rec = _first_record(p)
            except Exception:
                continue
            if not rec or rec.get("type") != "session_meta":
                continue
            meta = rec.get("payload")
            if not isinstance(meta, dict) or str(meta.get("cwd") or "") != want:
                continue
            # A subagent shares its parent's cwd; its id differs from the root
            # session_id. Measure the root thread, not a disposable child.
            if meta.get("parent_thread_id") or (
                    meta.get("session_id") and meta.get("id")
                    and meta["session_id"] != meta["id"]):
                continue
            return p
        return None

    def read(self, path: Path) -> Usage:
        used = 0
        window: int | None = None
        model: str | None = None
        for rec in iter_jsonl(path):
            payload = rec.get("payload")
            if not isinstance(payload, dict):
                continue
            kind = rec.get("type")
            if kind == "turn_context" and payload.get("model"):
                model = str(payload["model"])
            elif kind == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info")
                if not isinstance(info, dict):  # info is Option<_>: null early on
                    continue
                last = info.get("last_token_usage")
                if isinstance(last, dict) and last:
                    if "total_tokens" in last:
                        used = as_int(last.get("total_tokens"))
                    else:
                        used = as_int(last.get("input_tokens")) + as_int(last.get("output_tokens"))
                if as_int(info.get("model_context_window")) > 0:
                    window = as_int(info["model_context_window"])
        return Usage(used, window, model, "session" if window else None)

    def session_of(self, path: Path) -> str:
        """rollout-<stamp>-<uuid>[_<rollout_id>].jsonl -> <uuid>."""
        stem = path.name[: -len(".jsonl")] if path.name.endswith(".jsonl") else path.stem
        return "-".join(stem.split("-")[-5:]).split("_", 1)[0]


BACKEND = CodexBackend()
