"""Shared test helpers. Every test builds its environment from scratch: a
subprocess must never see the real HOME, CODEX_HOME, CLAUDE_CODE_SESSION_ID or
ORCA_* of the session running the tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "context-budget" / "scripts"
CTXCHECK = SCRIPTS / "ctxcheck"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

CLAUDE_SESSION = "11111111-2222-4333-8444-555555555555"
CODEX_THREAD = "019a0000-0000-7000-8000-00000000c0de"


def clean_env(tmp: str | Path, **extra: str) -> dict:
    """A complete environment rooted in `tmp`; nothing is inherited."""
    tmp = Path(tmp)
    home = tmp / "home"
    env = {
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "CODEX_HOME": str(home / ".codex"),
        "COPILOT_HOME": str(home / ".copilot"),
        "XDG_CONFIG_HOME": str(tmp / "xdg" / "config"),
        "XDG_DATA_HOME": str(tmp / "xdg" / "data"),
        "XDG_STATE_HOME": str(tmp / "xdg" / "state"),
        "XDG_CACHE_HOME": str(tmp / "xdg" / "cache"),
        "CTXCHECK_HOME": str(tmp / "ctxcheck"),
    }
    home.mkdir(parents=True, exist_ok=True)
    env.update(extra)
    return env


def run_ctxcheck(args: list[str], env: dict, cwd: str | Path | None = None,
                 stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CTXCHECK), *args],
        env=env, cwd=str(cwd) if cwd else env["HOME"], input=stdin,
        capture_output=True, text=True, timeout=60)


def install_claude_transcript(home: str | Path, session: str = CLAUDE_SESSION,
                              fixture: str = "session-basic.jsonl",
                              slug: str = "-synthetic-project") -> Path:
    dest = Path(home) / ".claude" / "projects" / slug / f"{session}.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "claude" / fixture, dest)
    return dest


def install_codex_rollout(codex_home: str | Path, thread: str = CODEX_THREAD,
                          cwd: str | Path = "/synthetic/project",
                          fixture: str = "rollout-basic.jsonl",
                          stamp: str = "2026-10-15T09-00-00",
                          session: str | None = None) -> Path:
    """Copy a rollout fixture into a dated sessions dir, filling placeholders.
    `session` sets session_meta.session_id (the root); default is the thread."""
    y, m, d = stamp[:10].split("-")
    dest = Path(codex_home) / "sessions" / y / m / d / f"rollout-{stamp}-{thread}.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = (FIXTURES / "codex" / fixture).read_text(encoding="utf-8")
    text = text.replace('"session_id":"__THREAD__"', f'"session_id":"{session or thread}"')
    text = text.replace("__THREAD__", thread).replace("__CWD__", str(cwd))
    dest.write_text(text, encoding="utf-8")
    return dest
