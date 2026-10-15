# Runtime support

`ctxcheck` measures a session from that runtime's own session data. It tries
runtimes in this order: Claude Code, Codex, OpenCode, Pi.

A runtime wins only if its session data actually resolves. Environment
variables leak into child agents, so a variable on its own proves nothing.
A resolving session id beats a runtime that only signals it is running
(`OPENCODE=1`, `PI_CODING_AGENT`). Such a runtime is used through its cwd
fallback, and only after every session id has been tried.

If nothing resolves, `ctxcheck` exits 3. Pass `--runtime R` to skip detection.

Vendor facts below were verified on 2026-10-15. Each backend module cites its
sources.

## Support matrix

| Runtime | How it's measured | Window source | Backstop |
| --- | --- | --- | --- |
| Claude Code | `CLAUDE_CODE_SESSION_ID` → `~/.claude/projects/*/<id>.jsonl`. Last non-sidechain assistant: `input + cache_creation_input + cache_read`. | Env, then model rules (1M or 200K) | pending |
| Codex CLI | `CODEX_THREAD_ID` → `$CODEX_HOME/sessions/**/rollout-*-<id>.jsonl`. Last `token_count` `last_token_usage.total_tokens`. Falls back to the newest root rollout for the cwd. | The session's `model_context_window` (already ×0.95) | pending |
| OpenCode v2 (2.0.x) | `OPENCODE_SESSION_ID` → `opencode.db` tables `session_v2` / `session_message`, opened read-only. Newest `assistant` row with `tokens` after the last completed `compaction`, before any revert point: `input + output + reasoning + cache.read + cache.write`. | `kv['models-dev:catalog']` → `limit.context`, else the v1 cache file, else a preset | pending |
| OpenCode v1 (1.19.x) | No session id; only `OPENCODE=1` is set. Newest top-level `session` whose `directory` is the cwd. Same database, tables `session` / `message`: newest assistant with `output > 0`, same five-field sum. | `$XDG_CACHE_HOME/opencode/models.json` (or `$OPENCODE_MODELS_PATH`) → `limit.context`, else a preset | pending |
| Pi (1.2.x) | `PI_SESSION_ID` / `PI_SESSION_FILE` → `<agent dir>/sessions/--<cwd slug>--/<stamp>_<id>.jsonl`. Walks the `parentId` chain from the last entry, so abandoned branches don't count. Latest non-aborted, non-error assistant `usage.totalTokens`, else `input + output + cacheRead + cacheWrite`. A `compaction` resets usage to 0 until the next response. | `PI_PROVIDER`/`PI_MODEL` (else the branch's model) → Pi's `models.json`, then `models-store.json`, then OpenCode's models.dev data. Otherwise `--window` / `CTXCHECK_WINDOW` / `CTXCHECK_PRESET` is required (exit 3). | pending |

The "Backstop" column records how a runtime gets the post-tool warning when an
armed session goes over its gate. Installers arrive in later versions.

## Where the data lives

**OpenCode.**
- The database is `${XDG_DATA_HOME:-~/.local/share}/opencode/opencode.db`.
  `$OPENCODE_DB` overrides it. Non-release channels write
  `opencode-<channel>.db` alongside it, and `ctxcheck` searches those too.
- The database is the user's live data. `ctxcheck` opens it only through a
  read-only `file:...?mode=ro` URI.
- One file holds every session, so `transcript` in the JSON output is a
  locator, `<db>#<session id>`.
- `--transcript` accepts either that locator or the bare database plus
  `--session`.
- After migrating, v2 keeps the v1 tables in the same file. A session id is
  looked up in `session_v2` first. The cwd fallback takes whichever table set
  has the newer session.

**Pi.**
- The agent dir is `$PI_CODING_AGENT_DIR`, default `~/.pi/agent`.
- Sessions live under `<agent dir>/sessions/--<cwd with / \ : turned into ->--/`.
- `PI_CODING_AGENT_SESSION_DIR` replaces that tree with a flat directory. There,
  the session for a cwd is matched by the header's `cwd`.
- Pi's tool environment sets `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`
  and `PI_MODEL` fresh for every command. It does not set them for
  user-typed `!` commands.

## Known gaps

- **OpenCode window.** Custom providers configured only in `opencode.json`
  aren't in the models.dev catalog. Their window falls back to a preset, so
  pass `--window` for them.
- **OpenCode v1.** With two OpenCode sessions open in one directory, the cwd
  fallback picks the most recently updated one.
- **Pi.**
  - Pi itself shows the count as unknown after a compaction. `ctxcheck`
    reports 0 until the next response.
  - A `context_edit` is treated the same way.
