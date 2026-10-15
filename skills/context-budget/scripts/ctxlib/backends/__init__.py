"""Per-runtime readers. Each module exposes a BACKEND object satisfying
ctxlib.runtime.Backend; shared transcript helpers live here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from .. import CtxError


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed JSONL records, skipping blank, malformed and non-object
    lines (a partially flushed tail line is normal while a session runs)."""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError as e:
        raise CtxError(f"cannot read {path}: {e}") from e
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                yield rec


def newest(paths: list[Path]) -> Path | None:
    """The most recently modified path, ignoring ones that vanished."""
    stamped = []
    for p in paths:
        try:
            stamped.append((p.stat().st_mtime, p))
        except OSError:
            continue
    stamped.sort(key=lambda t: t[0], reverse=True)
    return stamped[0][1] if stamped else None


def as_int(value: object) -> int:
    """A token count from JSON, tolerating null, floats and junk."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0
