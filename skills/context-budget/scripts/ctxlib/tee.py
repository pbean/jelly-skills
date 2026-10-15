"""`ctxcheck statusline-tee --runtime R [-- <wrapped cmd...>]`

Installed as an agent's status-line command (see install.py). The agent pipes
its status JSON to us on every refresh; we snapshot it atomically to
statusline/R/<session>.json for the backends.statusline readers, then hand the
same bytes to the user's original status-line command and relay its output
and exit code. Without a wrapped command we print `ctx NN%`.

The tee must never break the status line: every failure while snapshotting is
swallowed, and the wrapped command runs regardless. A single wrapped argument
is a shell command line (that is how install-statusline quotes the original,
so pipes and `~` in it keep working); several are an argv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

from .paths import statusline_dir


def _split(args: Sequence[str]) -> tuple[list[str], list[str]]:
    args = list(args)
    if "--" in args:
        i = args.index("--")
        return args[:i], args[i + 1:]
    return args, []


def _runtime(opts: list[str]) -> str | None:
    """--runtime R / --runtime=R, without argparse (which exits on errors)."""
    for i, a in enumerate(opts):
        if a == "--runtime" and i + 1 < len(opts):
            return opts[i + 1]
        if a.startswith("--runtime="):
            return a.split("=", 1)[1]
    return None


def snapshot(runtime: str, data: bytes) -> Path | None:
    """Write {received_at, cwd, runtime, session, payload}; None if the
    payload isn't a JSON object. Raises on I/O errors (the caller swallows)."""
    from .backends.statusline import payload_cwd, payload_session, safe_name

    if not runtime or not runtime.replace("-", "").isalnum():
        return None
    payload = json.loads(data.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        return None
    cwd = payload_cwd(payload) or os.getcwd()
    session = payload_session(payload)
    # Cursor-style payloads without an id still get a stable per-cwd file.
    name = safe_name(session) if session else "cwd-" + safe_name(cwd.strip("/") or "root")
    record = {"received_at": time.time(), "cwd": cwd, "runtime": runtime,
              "session": session, "payload": payload}
    d = statusline_dir(runtime)
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tee-", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        dest = d / f"{name}.json"
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def _fallback_line(data: bytes) -> str:
    try:
        from .backends.statusline import extract
        used, size, _ = extract(json.loads(data.decode("utf-8", errors="replace")))
        if used is not None and size:
            return f"ctx {used / size * 100:.0f}%"
    except Exception:
        pass
    return "ctx --%"


def main(argv: Sequence[str]) -> int:
    opts, wrapped = _split(argv)
    try:  # a terminal is not a payload; don't wait on one
        data = b"" if sys.stdin.isatty() else sys.stdin.buffer.read()
    except Exception:
        data = b""
    try:
        snapshot(_runtime(opts) or "", data)
    except Exception:
        pass  # never let the sidecar break the status line

    out = sys.stdout.buffer
    if not wrapped:
        out.write((_fallback_line(data) + "\n").encode())
        out.flush()
        return 0
    cmd = wrapped[0] if len(wrapped) == 1 else wrapped
    try:
        proc = subprocess.run(cmd, shell=len(wrapped) == 1, input=data,
                              stdout=subprocess.PIPE)
    except Exception as e:
        print(f"statusline-tee: cannot run wrapped command: {e}", file=sys.stderr)
        out.write((_fallback_line(data) + "\n").encode())
        out.flush()
        return 127
    out.write(proc.stdout)
    out.flush()
    return proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
