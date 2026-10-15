"""Which runtime is running us, and where its session data is.

Candidates are tried in a fixed order (Claude, Codex, OpenCode, Pi, Copilot,
Antigravity, Cursor). A candidate wins only if its session
data actually resolves: env vars leak into child agents - a Codex launched
from a Claude shell inherits CLAUDE_CODE_SESSION_ID - so an env var alone
proves nothing. When nothing resolves the session is unmeasurable (exit 3).

Some runtimes give a child process no session id, only a marker that it is
running inside them (OpenCode v1 sets OPENCODE=1). A backend with a `present()`
method returning True is then tried through its cwd fallback, after every
candidate with a session id.

A "transcript" is whatever path a backend's read() takes. For OpenCode it is a
locator, `<opencode.db>#<session id>`, because one database holds every
session; a backend may define `transcript_file(path)` to name the real file
behind its locator and `locator(path, session)` to build one from a bare file. For the status-line
runtimes (Copilot, Antigravity, Cursor) it is a sidecar snapshot the
`statusline-tee` wrote, and the backend says so with `measure_source`.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Optional

try:  # typing.Protocol is 3.8+, but keep the import failure-proof.
    from typing import Protocol
except ImportError:  # pragma: no cover
    Protocol = object  # type: ignore[assignment,misc]

from . import CtxError, Unmeasurable


class Usage(NamedTuple):
    used: int
    window: Optional[int]
    model: Optional[str]
    # Where `window` came from ("session", "model rule", an env var name...);
    # None when the backend could not tell and a preset must supply it.
    window_source: Optional[str] = None
    # Age of the data in seconds, for snapshots (the status-line sidecar);
    # None for a transcript, which is the live record.
    stale_seconds: Optional[float] = None


class Backend(Protocol):
    name: str
    session_env: str  # the env var naming the live session, for messages

    def session_id(self) -> str | None: ...
    def find(self, session: str) -> Path | None: ...
    def fallback(self, cwd: Path) -> Path | None: ...
    def read(self, path: Path) -> Usage: ...
    def session_of(self, path: Path) -> str: ...
    # Optional: present() -> bool, transcript_file(path) -> Path,
    # locator(path, session) -> Path, and the attribute implicit_preset
    # (False: never fall back to the default preset; see cli.resolve_window)
    # and the attribute measure_source (default "transcript").


class Resolved(NamedTuple):
    backend: Backend
    session: Optional[str]
    path: Path
    measure_source: str  # "transcript" | "statusline"


def candidates() -> list[Backend]:
    """Every backend, in detection order. Imported lazily: backends import
    Usage from this module."""
    from .backends import claude, codex, opencode, pi, statusline
    return [claude.BACKEND, codex.BACKEND, opencode.BACKEND, pi.BACKEND,
            *statusline.BACKENDS]


def names() -> tuple[str, ...]:
    return tuple(b.name for b in candidates())


def by_name(name: str) -> Backend:
    for b in candidates():
        if b.name == name:
            return b
    raise CtxError(f"unknown runtime {name!r}; one of: {', '.join(names())}")


def _sniff(path: Path) -> Backend | None:
    """Guess a transcript's runtime from where it lives and what it's called."""
    if path.name.startswith("rollout-"):
        return by_name("codex")
    if "/.claude/projects/" in path.as_posix():
        return by_name("claude")
    if path.suffix == ".db" and path.name.startswith("opencode"):
        return by_name("opencode")
    if "/.pi/agent/sessions/" in path.as_posix():
        return by_name("pi")
    from .paths import statusline_dir
    try:
        rel = path.resolve().relative_to(statusline_dir().resolve())
    except ValueError:
        return None
    return by_name(rel.parts[0]) if len(rel.parts) == 2 and rel.parts[0] in names() else None


def _resolved(backend: Backend, session: str | None, path: Path) -> Resolved:
    return Resolved(backend, session, path,
                    getattr(backend, "measure_source", "transcript"))


def _present(backend: Backend) -> bool:
    probe = getattr(backend, "present", None)
    return bool(probe and probe())


def _locate(backend: Backend, session: str | None, cwd: Path) -> Path | None:
    if session:
        found = backend.find(session)
        if found:
            return found
    return backend.fallback(cwd)


def resolve(requested: str = "auto", session: str | None = None,
            transcript: str | None = None, cwd: Path | None = None) -> Resolved:
    """Pick the runtime and its transcript, or raise Unmeasurable/CtxError."""
    cwd = cwd or Path.cwd()

    if transcript:
        path = Path(transcript).expanduser()
        file = Path(transcript.split("#", 1)[0]).expanduser()
        if not file.is_file():
            raise CtxError(f"--transcript {file}: no such file")
        if requested != "auto":
            backend = by_name(requested)
        else:
            backend = (_sniff(file)
                       or next((b for b in candidates() if b.session_id()), None))
            if backend is None:
                raise CtxError(f"cannot tell which runtime wrote {file}; pass --runtime")
        sid = session or backend.session_id()
        locator = getattr(backend, "locator", None)
        if locator:
            path = locator(path, sid)
        return _resolved(backend, sid, path)

    if requested != "auto":
        backend = by_name(requested)
        sid = session or backend.session_id()
        path = _locate(backend, sid, cwd)
        if path:
            return _resolved(backend, sid, path)
        what = f"session {sid}" if sid else f"${backend.session_env} unset, nothing for cwd {cwd}"
        raise Unmeasurable(f"no {backend.name} session data found ({what})",
                           [f"{backend.name}: {what}"])

    # Pass 1: a session id that resolves. Pass 2: a runtime that only says it
    # is present, through its cwd fallback - weaker evidence (OPENCODE=1 leaks
    # into child agents too), so any resolving session id beats it.
    tried: list[str] = []
    weak: list[Backend] = []
    for backend in candidates():
        sid = session or backend.session_id()
        if not sid:
            if _present(backend):
                weak.append(backend)
            else:
                tried.append(f"{backend.name}: ${backend.session_env} unset")
            continue
        path = _locate(backend, sid, cwd)
        if path:
            return _resolved(backend, sid, path)
        tried.append(f"{backend.name}: ${backend.session_env}={sid} has no session data")
    for backend in weak:
        path = backend.fallback(cwd)
        if path:
            return _resolved(backend, None, path)
        tried.append(f"{backend.name}: ${backend.session_env} unset and no session for cwd {cwd}")
    raise Unmeasurable("no runtime's session data resolved; this session cannot be measured",
                       tried)
