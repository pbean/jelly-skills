"""Claude Code: CLAUDE_CODE_SESSION_ID -> ~/.claude/projects/<slug>/<id>.jsonl

Used tokens reproduce the status line's context_window.used_percentage, read
off the most recent assistant message:

    used = input_tokens + cache_creation_input_tokens + cache_read_input_tokens

Output tokens are excluded. Two things make a naive version wrong:

  * Subagent turns land in the same transcript with their own `usage` and
    `isSidechain: true`. They hold a separate context; counting one reports a
    disposable subagent's usage as this session's. They are skipped.
  * The window is not recorded in the transcript, and the model field carries
    no `[1m]` suffix, so it is derived from env plus model rules (window_for).

The transcript format is officially internal; parse tolerantly.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping

from ..runtime import Usage
from . import as_int, iter_jsonl, newest

NAME = "claude"
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
# verified 2026-10-15: https://code.claude.com/docs/en/env-vars - set to the
# current session id in Bash tool, hook and stdio MCP subprocesses; matches the
# hook JSON's session_id and follows /clear.

ONE_M, TWO_HUNDRED_K = 1_000_000, 200_000

# Claude Code writes non-API messages (errors, interrupts) as assistant records
# with this model and zeroed usage; they say nothing about the context.
SYNTHETIC_MODEL = "<synthetic>"

# claude-opus-5-5, claude-haiku-4-5-20251001, us.anthropic.claude-opus-4-8-v1:0
_NEW_STYLE = re.compile(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d{1,2}))?(?!\d)")
# claude-3-5-sonnet-20241022, claude-3-opus-20240229 (all 200K-era models)
_OLD_STYLE = re.compile(r"claude-\d+(?:-\d{1,2})?-(opus|sonnet|haiku)")


def _parse_model(model: str) -> tuple[str, int, int] | None:
    m = _NEW_STYLE.search(model)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3) or 0)
    m = _OLD_STYLE.search(model)
    if m:
        return m.group(1), 0, 0
    return None


def _defaults_to_1m(family: str, version: tuple[int, int]) -> bool:
    # verified 2026-10-15: https://code.claude.com/docs/en/model-config -
    # "Fable 5.1, Fable 5, Sonnet 5 and later, Haiku 5.5, and Opus 4.7 and
    # later run with the 1M window by default, with no [1m] suffix needed."
    # Opus/Sonnet 4.6 reach 1M only via their [1m] variant; older models are
    # not 1M-capable.
    if family == "fable":
        return version >= (5, 0)
    if family == "sonnet":
        return version >= (5, 0)
    if family == "haiku":
        return version >= (5, 5)
    if family == "opus":
        return version >= (4, 7)
    return False


def _positive_int(raw: str | None) -> int | None:
    try:
        n = int(raw) if raw else 0
    except ValueError:
        return None
    return n if n > 0 else None


def window_for(model: str | None, env: Mapping[str, str]) -> tuple[int | None, str | None]:
    """(window, source) for a Claude session, or (None, None) when unknown.

    verified 2026-10-15: https://code.claude.com/docs/en/env-vars and
    https://code.claude.com/docs/en/model-config (Claude Code >= 2.1.193):
      * CLAUDE_CODE_MAX_CONTEXT_TOKENS overrides the assumed window for an
        unrecognized model id; for a recognized id it takes effect only with
        DISABLE_COMPACT set.
      * CLAUDE_CODE_DISABLE_1M_CONTEXT=1 holds 1M-default models to 200K.
      * An unrecognized id runs at 200K unless it carries [1m].
    """
    max_tokens = _positive_int(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"))
    if not model:  # no assistant turn yet; the override is the best guess
        return (max_tokens, "CLAUDE_CODE_MAX_CONTEXT_TOKENS") if max_tokens else (None, None)
    parsed = _parse_model(model)
    disable_1m = env.get("CLAUDE_CODE_DISABLE_1M_CONTEXT") == "1"
    if max_tokens:
        # An unrecognized [1m] id is assumed 1M unless 1M is disabled.
        if parsed is None and not ("[1m]" in model and not disable_1m):
            return max_tokens, "CLAUDE_CODE_MAX_CONTEXT_TOKENS"
        if parsed is not None and env.get("DISABLE_COMPACT") == "1":
            return max_tokens, "CLAUDE_CODE_MAX_CONTEXT_TOKENS"
    if disable_1m:
        return TWO_HUNDRED_K, "CLAUDE_CODE_DISABLE_1M_CONTEXT"
    if "[1m]" in model:
        return ONE_M, "model rule ([1m] variant)"
    if parsed is None:
        return TWO_HUNDRED_K, "model rule (unrecognized id)"
    family, major, minor = parsed
    # "Recognized" also matches provider-pinned ids that contain the API id.
    if _defaults_to_1m(family, (major, minor)):
        return ONE_M, "model rule"
    return TWO_HUNDRED_K, "model rule"


class ClaudeBackend:
    name = NAME
    session_env = SESSION_ENV

    def session_id(self) -> str | None:
        return os.environ.get(SESSION_ENV) or None

    def projects_root(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def find(self, session: str) -> Path | None:
        """Locate a transcript without reconstructing the project slug."""
        return newest(list(self.projects_root().glob(f"*/{session}.jsonl")))

    def fallback(self, cwd: Path) -> Path | None:
        return None

    def read(self, path: Path) -> Usage:
        usage: dict = {}
        model: str | None = None
        for rec in iter_jsonl(path):
            if rec.get("type") != "assistant" or rec.get("isSidechain"):
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            u = msg.get("usage")
            if not isinstance(u, dict) or not u or msg.get("model") == SYNTHETIC_MODEL:
                continue
            usage, model = u, msg.get("model") or model
        used = (as_int(usage.get("input_tokens"))
                + as_int(usage.get("cache_creation_input_tokens"))
                + as_int(usage.get("cache_read_input_tokens")))
        window, source = window_for(model, os.environ)
        return Usage(used, window, model, source)

    def session_of(self, path: Path) -> str:
        return path.stem


BACKEND = ClaudeBackend()
