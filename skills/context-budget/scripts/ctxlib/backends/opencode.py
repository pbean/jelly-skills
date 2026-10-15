"""OpenCode v1 and v2: one SQLite database holds every session.

    ${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db
    (or $OPENCODE_DB; non-release channels use opencode-<channel>.db)

The database is the user's live data. It is only ever opened read-only, via a
`file:...?mode=ro` URI. Since one file holds every session, the "transcript"
this backend hands around is a locator, `<db path>#<session id>`.

v2 (2.0.x; Orca agent `opencode2`) - tables session_v2 / session_message / kv.
  * The shell tool sets OPENCODE_SESSION_ID (and OPENCODE=1).
  * session_message has `type` and `seq` columns; `data` is the rest of the
    message as JSON. Used tokens mirror the TUI's contextUsage: after the last
    `compaction` row whose status is "completed" (and before the session's
    revert point, if any), the newest `assistant` row with `tokens`:
        tokens.input + output + reasoning + cache.read + cache.write
  * Window: kv['models-dev:catalog'] is {updatedAt, digest, body}, where body
    is the models.dev api.json as a *string*; the TUI looks the assistant's
    model.{providerID, id} up there and uses limit.context.

v1 (1.19.x; Orca agent `opencode`) - tables session / message / part.
  * No session id reaches child processes, only OPENCODE=1, so the session is
    the newest top-level `session` whose directory is the cwd.
  * message.data carries `role`; used tokens mirror the sidebar: the newest
    assistant with tokens.output > 0, same five-field sum.
  * Window: the models.dev cache file $XDG_CACHE_HOME/opencode/models.json
    (or $OPENCODE_MODELS_PATH), the same api.json shape, parsed once.

v2 keeps the v1 tables in the same file after migrating them, so a database
with session_v2 is read as v2 first; v1 tables are still consulted for a
session id v2 doesn't know, and the cwd fallback takes whichever is newer.

When no catalog knows the model, the window falls back to a preset (or
--window / CTXCHECK_WINDOW).

verified 2026-10-15 against anomalyco/opencode@v2.0.29 and @v1.19.0:
  v2: packages/core/src/session/sql.ts L22-98 (session_v2, session_message),
    packages/core/src/kv/sql.ts L5-9 (kv), packages/core/src/session/
    projector.ts L395-410 (id/type stripped from data), packages/schema/src/
    session-message.ts L212-282 (assistant model ref, compaction status),
    token-usage.ts L6-14 (tokens), packages/tui/src/util/session.ts L46-61 +
    packages/client/src/solid/usage.ts L8-22 (contextUsage), packages/core/
    src/models-dev.ts L269-302, L369-376 (catalog kv value), packages/core/src/
    tool/plugin/shell.ts L213-216 (OPENCODE_SESSION_ID, OPENCODE=1),
    packages/cli/src/database-path.ts L4-13 (OPENCODE_DB, channel file names),
    packages/util/src/global-roots.ts L5-13 (XDG data dir).
  v1: packages/core/src/session/sql.ts L22-98 (session, message, part),
    packages/schema/src/v1/session.ts L453-485 (assistant: role, providerID,
    modelID, tokens), packages/tui/src/feature-plugins/sidebar/context.tsx
    L19-35 (output > 0 rule), packages/core/src/models-dev.ts L160-215
    (cache path), packages/opencode/src/index.ts L75-77 (OPENCODE=1; no
    session id), packages/core/src/database/database.ts L43-55 (db path).
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .. import CtxError
from ..runtime import Usage
from . import as_int

NAME = "opencode"
SESSION_ENV = "OPENCODE_SESSION_ID"
CATALOG_KEY = "models-dev:catalog"


def data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "opencode"


def databases() -> list[Path]:
    """Candidate databases: $OPENCODE_DB alone when set, else opencode.db and
    any channel databases (opencode-<channel>.db) beside it."""
    d = data_dir()
    env = os.environ.get("OPENCODE_DB")
    if env:
        p = Path(env).expanduser()
        return [p if p.is_absolute() else d / p]
    found = [d / "opencode.db"] + sorted(d.glob("opencode-*.db"))
    return [p for p in found if p.is_file()]


def connect(db: Path) -> sqlite3.Connection:
    """Read-only, so a live OpenCode's database can never be written."""
    con = sqlite3.connect(f"file:{quote(str(db))}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _tables(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _json(text: object) -> dict:
    if not isinstance(text, (str, bytes)):
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def split_locator(path: Path) -> tuple[Path, str]:
    db, sep, sid = str(path).rpartition("#")
    if not sep or not sid:
        raise CtxError(f"{path}: not an OpenCode session locator (<db>#<session id>)")
    return Path(db), sid


def _five(tokens: dict) -> int:
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return (as_int(tokens.get("input")) + as_int(tokens.get("output"))
            + as_int(tokens.get("reasoning"))
            + as_int(cache.get("read")) + as_int(cache.get("write")))


# --- models.dev catalogs ---------------------------------------------------

def _lookup(catalog: dict, provider: str, model: str) -> int | None:
    """limit.context for provider/model in a models.dev api.json map. OpenCode
    builds ids from each entry's `id` field; map keys are the fallback."""
    for pkey, prov in catalog.items():
        if not isinstance(prov, dict) or provider not in (prov.get("id"), pkey):
            continue
        models = prov.get("models")
        if not isinstance(models, dict):
            continue
        for mkey, m in models.items():
            if isinstance(m, dict) and model in (m.get("id"), mkey):
                limit = m.get("limit")
                window = as_int(limit.get("context")) if isinstance(limit, dict) else 0
                if window > 0:
                    return window
    return None


def _kv_catalog(con: sqlite3.Connection) -> dict:
    if "kv" not in _tables(con):
        return {}
    row = con.execute("SELECT value FROM kv WHERE key = ?", (CATALOG_KEY,)).fetchone()
    if not row:
        return {}
    value = _json(row[0])
    body = value.get("body")
    return _json(body) if isinstance(body, str) else (body if isinstance(body, dict) else {})


def _cache_file() -> Path:
    env = os.environ.get("OPENCODE_MODELS_PATH")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "opencode" / "models.json"


def _cache_window(provider: str, model: str) -> tuple[int, str] | None:
    try:
        catalog = _json(_cache_file().read_text(encoding="utf-8"))
    except OSError:
        return None
    window = _lookup(catalog, provider, model)
    return (window, "opencode models.dev cache") if window else None


def _kv_window(con: sqlite3.Connection, provider: str, model: str) -> tuple[int, str] | None:
    window = _lookup(_kv_catalog(con), provider, model)
    return (window, "opencode models.dev catalog") if window else None


def models_dev_window(provider: str, model: str) -> tuple[int, str] | None:
    """The window OpenCode's models.dev data gives provider/model: a v2
    database's kv catalog, else v1's cache file. Also used by Pi."""
    for db in databases():
        try:
            con = connect(db)
            try:
                found = _kv_window(con, provider, model)
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if found:
            return found
    return _cache_window(provider, model)


# --- reading a session -----------------------------------------------------

def _read_v2(con: sqlite3.Connection, sid: str) -> tuple[int, str | None, str | None]:
    session = con.execute("SELECT revert FROM session_v2 WHERE id = ?", (sid,)).fetchone()
    stop_at = _json(session["revert"]).get("messageID") if session else None
    used, provider, model = 0, None, None
    rows = con.execute("SELECT id, type, data FROM session_message "
                       "WHERE session_id = ? ORDER BY seq", (sid,))
    for row in rows:
        if stop_at and row["id"] == stop_at:
            break
        data = _json(row["data"])
        if row["type"] == "compaction" and data.get("status") == "completed":
            used, provider, model = 0, None, None
        elif row["type"] == "assistant" and isinstance(data.get("tokens"), dict):
            used = _five(data["tokens"])
            ref = data.get("model") if isinstance(data.get("model"), dict) else {}
            provider, model = ref.get("providerID"), ref.get("id")
    return used, provider, model


def _read_v1(con: sqlite3.Connection, sid: str) -> tuple[int, str | None, str | None]:
    used, provider, model = 0, None, None
    rows = con.execute("SELECT data FROM message WHERE session_id = ? "
                       "ORDER BY time_created, id", (sid,))
    for row in rows:
        data = _json(row["data"])
        tokens = data.get("tokens")
        if (data.get("role") == "assistant" and isinstance(tokens, dict)
                and as_int(tokens.get("output")) > 0):
            used = _five(tokens)
            provider, model = data.get("providerID"), data.get("modelID")
    return used, provider, model


def _schema_of(con: sqlite3.Connection, sid: str) -> str | None:
    """'v2' or 'v1' for the table set that holds session `sid`."""
    tables = _tables(con)
    if {"session_v2", "session_message"} <= tables and con.execute(
            "SELECT 1 FROM session_v2 WHERE id = ?", (sid,)).fetchone():
        return "v2"
    if {"session", "message"} <= tables and con.execute(
            "SELECT 1 FROM session WHERE id = ?", (sid,)).fetchone():
        return "v1"
    return None


def _newest_in(con: sqlite3.Connection, table: str, cwd: str) -> tuple[int, str] | None:
    row = con.execute(f"SELECT id, time_updated FROM {table} "  # table is ours, not input
                      "WHERE directory = ? AND parent_id IS NULL "
                      "ORDER BY time_updated DESC LIMIT 1", (cwd,)).fetchone()
    return (as_int(row["time_updated"]), row["id"]) if row else None


class OpenCodeBackend:
    name = NAME
    session_env = SESSION_ENV

    def present(self) -> bool:
        return bool(os.environ.get("OPENCODE"))

    def session_id(self) -> str | None:
        return os.environ.get(SESSION_ENV) or None

    def find(self, session: str) -> Path | None:
        for db in databases():
            try:
                con = connect(db)
                try:
                    if _schema_of(con, session):
                        return Path(f"{db}#{session}")
                finally:
                    con.close()
            except sqlite3.Error:
                continue
        return None

    def fallback(self, cwd: Path) -> Path | None:
        """Newest top-level session (subagents are child sessions with a
        parent_id) whose directory is `cwd`, across both table sets."""
        want = str(cwd)
        best: tuple[int, str, Path] | None = None
        for db in databases():
            try:
                con = connect(db)
                try:
                    tables = _tables(con)
                    for table in ("session_v2", "session"):
                        if table not in tables:
                            continue
                        hit = _newest_in(con, table, want)
                        if hit and (best is None or hit[0] > best[0]):
                            best = (hit[0], hit[1], db)
                finally:
                    con.close()
            except sqlite3.Error:
                continue
        return Path(f"{best[2]}#{best[1]}") if best else None

    def locator(self, path: Path, session: str | None) -> Path:
        """--transcript accepts a locator or a bare database plus a session."""
        if "#" in str(path):
            return path
        if not session:
            raise CtxError(f"--transcript {path} is a whole OpenCode database; "
                           f"pass --session or set ${SESSION_ENV}")
        return Path(f"{path}#{session}")

    def read(self, path: Path) -> Usage:
        db, sid = split_locator(path)
        try:
            con = connect(db)
            try:
                schema = _schema_of(con, sid)
                if schema is None:
                    raise CtxError(f"no OpenCode session {sid} in {db}")
                reader = _read_v2 if schema == "v2" else _read_v1
                used, provider, model = reader(con, sid)
                window = None
                if provider and model:
                    window = _kv_window(con, provider, model) or _cache_window(provider, model)
            finally:
                con.close()
        except sqlite3.Error as e:
            raise CtxError(f"cannot read {db}: {e}") from e
        label = f"{provider}/{model}" if provider and model else model
        if window:
            return Usage(used, window[0], label, window[1])
        return Usage(used, None, label, None)

    def session_of(self, path: Path) -> str:
        return split_locator(path)[1]


BACKEND = OpenCodeBackend()
