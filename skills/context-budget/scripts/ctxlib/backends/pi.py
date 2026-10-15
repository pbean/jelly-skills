"""Pi (@earendil-works/pi-coding-agent): PI_SESSION_FILE / PI_SESSION_ID ->
<agent dir>/sessions/--<cwd slug>--/<stamp>_<id>.jsonl

A session file is a JSONL tree. The first line is a `session` header (id, cwd)
outside the tree; every other entry has `id` and `parentId`. No leaf pointer
is stored: the leaf is the last entry in the file, and the live branch is the
`parentId` chain from it back to a root. Entries on abandoned sibling branches
(left by /tree navigation) are not part of the context and are ignored.

Used tokens follow Pi's own context accounting on that branch:

  * The latest assistant `message` whose stopReason is neither "aborted" nor
    "error" and whose usage is non-zero supplies the count:
        usage.totalTokens, else input + output + cacheRead + cacheWrite
    (calculateContextTokens).
  * A `compaction` (or `context_edit`) on the branch invalidates every usage
    before it. Pi then shows the count as unknown until the next response; we
    report 0 used, since the compacted context is small and a gate must not
    fire on stale pre-compaction numbers.

The window is never on disk with the session. It is looked up by provider and
model (PI_PROVIDER / PI_MODEL, else the branch's latest assistant message or
model_change) in Pi's own model files, then in OpenCode's models.dev cache. If
none knows the model, ctxcheck has no default for Pi (its models span every
vendor) and asks for --window / CTXCHECK_WINDOW (implicit_preset = False).

verified 2026-10-15 against earendil-works/pi@v1.2.0 (06a4669; npm latest
1.2.0), paths under packages/coding-agent/ unless noted:
  docs/session-format.md L53-137, L224-248 (header, entry types, tree,
    compaction, context building);
  src/core/session-manager.ts L403-415, L1103-1111 (leaf = last entry; walk
    parentId), L589-594 (cwd slug), L749-761 (custom session dir: match header
    cwd), L1079-1080 (file name <stamp>_<id>.jsonl), L1756 (custom dir has no
    per-cwd subfolder);
  src/core/compaction/compaction.ts L140-142 (calculateContextTokens),
    L148-175 (skip aborted/error/zero usage), L246-253 (usage before the
    latest compaction/context_edit is not trusted);
  src/core/agent-session.ts L4269-4294 (count is null after a compaction until
    a new response);
  src/core/tools/bash.ts L194-212 (PI_SESSION_ID, PI_SESSION_FILE, PI_PROVIDER,
    PI_MODEL set fresh for every tool command); src/cli/setup.ts L6-7
    (PI_CODING_AGENT="true" process-wide);
  src/config.ts L585-611 (PI_CODING_AGENT_DIR, default ~/.pi/agent);
    src/main.ts L690-694 (PI_CODING_AGENT_SESSION_DIR);
  src/core/models-store.ts L52, packages/ai/src/models-store.ts L3-15
    (<agent dir>/models-store.json: {provider: {models: [{id, contextWindow}]}});
  src/core/model-config.ts L43, L59 (<agent dir>/models.json providers.<p>.models[]).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..runtime import Usage
from . import as_int, iter_jsonl, newest

NAME = "pi"
SESSION_ENV = "PI_SESSION_ID"

# Entries after which earlier assistant usage no longer describes the context.
_BOUNDARIES = ("compaction", "context_edit")
_SKIP_STOP = ("aborted", "error")


def agent_dir() -> Path:
    env = os.environ.get("PI_CODING_AGENT_DIR")
    return Path(env).expanduser() if env else Path.home() / ".pi" / "agent"


def cwd_slug(cwd: Path) -> str:
    """`--<cwd minus its leading separator, with / \\ : turned into ->--`.
    Pi uses path.resolve (no symlink resolution), hence abspath."""
    resolved = os.path.abspath(str(cwd))
    return "--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", resolved)) + "--"


def context_tokens(usage: dict) -> int:
    total = as_int(usage.get("totalTokens"))
    if total:
        return total
    return sum(as_int(usage.get(k)) for k in ("input", "output", "cacheRead", "cacheWrite"))


def _header(path: Path) -> dict | None:
    for rec in iter_jsonl(path):
        return rec if rec.get("type") == "session" else None
    return None


def branch(entries: list[dict]) -> list[dict]:
    """The live branch, root first: the parentId chain from the last entry."""
    by_id = {e["id"]: e for e in entries if isinstance(e.get("id"), str)}
    path: list[dict] = []
    node = entries[-1] if entries else None
    seen: set[str] = set()
    while node is not None:
        nid = node.get("id")
        if nid in seen:  # a corrupt cycle; stop rather than spin
            break
        if isinstance(nid, str):
            seen.add(nid)
        path.append(node)
        parent = node.get("parentId")
        node = by_id.get(parent) if isinstance(parent, str) else None
    path.reverse()
    return path


def _model_windows(provider: str, model: str) -> tuple[int, str] | None:
    """Pi's own model files: models.json overrides, then the refreshed
    models-store.json catalog."""
    d = agent_dir()
    for fname in ("models.json", "models-store.json"):
        try:
            data = json.loads((d / fname).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        prov = data.get("providers", data) if fname == "models.json" else data
        entry = prov.get(provider) if isinstance(prov, dict) else None
        models = entry.get("models") if isinstance(entry, dict) else None
        if not isinstance(models, list):
            continue
        for m in models:
            if isinstance(m, dict) and m.get("id") == model:
                window = as_int(m.get("contextWindow"))
                if window > 0:
                    return window, f"pi {fname}"
    return None


def window_for(provider: str | None, model: str | None) -> tuple[int, str] | None:
    if not provider or not model:
        return None
    found = _model_windows(provider, model)
    if found:
        return found
    from . import opencode  # OpenCode's models.dev cache, if this machine has one
    return opencode.models_dev_window(provider, model)


class PiBackend:
    name = NAME
    session_env = SESSION_ENV
    implicit_preset = False

    def present(self) -> bool:
        return bool(os.environ.get("PI_CODING_AGENT") or os.environ.get("PI_SESSION_FILE"))

    def session_id(self) -> str | None:
        sid = os.environ.get(SESSION_ENV)
        if sid:
            return sid
        file = os.environ.get("PI_SESSION_FILE")
        if file and Path(file).is_file():
            return self.session_of(Path(file))
        return None

    def sessions_root(self) -> Path:
        env = os.environ.get("PI_CODING_AGENT_SESSION_DIR")
        return Path(env).expanduser() if env else agent_dir() / "sessions"

    def find(self, session: str) -> Path | None:
        file = os.environ.get("PI_SESSION_FILE")
        if file:
            p = Path(file)
            if p.is_file() and self.session_of(p) == session:
                return p
        root = self.sessions_root()
        return newest(list(root.glob(f"*_{session}.jsonl"))
                      + list(root.glob(f"*/*_{session}.jsonl")))

    def fallback(self, cwd: Path) -> Path | None:
        """Newest session started in `cwd`. A custom session dir is flat, so
        sessions there are told apart by the header's cwd."""
        if os.environ.get("PI_CODING_AGENT_SESSION_DIR"):
            want = os.path.abspath(str(cwd))
            hits = []
            for p in self.sessions_root().glob("*.jsonl"):
                try:
                    head = _header(p)
                except Exception:
                    continue
                if head and head.get("cwd") == want:
                    hits.append(p)
            return newest(hits)
        return newest(list((self.sessions_root() / cwd_slug(cwd)).glob("*.jsonl")))

    def read(self, path: Path) -> Usage:
        entries = [e for e in iter_jsonl(path) if e.get("type") != "session"]
        used = 0
        model: str | None = None
        provider: str | None = None
        for e in branch(entries):
            kind = e.get("type")
            if kind in _BOUNDARIES:
                used = 0
            elif kind == "model_change":
                provider = e.get("provider") or provider
                model = e.get("modelId") or model
            elif kind == "message":
                msg = e.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                provider = msg.get("provider") or provider
                model = msg.get("model") or model
                usage = msg.get("usage")
                if msg.get("stopReason") in _SKIP_STOP or not isinstance(usage, dict):
                    continue
                tokens = context_tokens(usage)
                if tokens > 0:
                    used = tokens
        # The tool env names the model selected right now; prefer it.
        provider = os.environ.get("PI_PROVIDER") or provider
        model = os.environ.get("PI_MODEL") or model
        found = window_for(provider, model)
        label = f"{provider}/{model}" if provider and model else model
        if found:
            return Usage(used, found[0], label, found[1])
        return Usage(used, None, label, None)

    def session_of(self, path: Path) -> str:
        try:
            head = _header(path)
        except Exception:
            head = None
        if head and isinstance(head.get("id"), str):
            return head["id"]
        stem = path.name[: -len(".jsonl")] if path.name.endswith(".jsonl") else path.stem
        return stem.split("_", 1)[-1]


BACKEND = PiBackend()
