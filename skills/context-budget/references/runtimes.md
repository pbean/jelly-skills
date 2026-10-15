# Runtime support

`ctxcheck` measures a session from that runtime's own session data. It tries
runtimes in this order: Claude Code, Codex, OpenCode, Pi, Copilot CLI,
Antigravity, Cursor CLI. The last three keep their live count in memory only,
so they are measured from a status-line sidecar (see "Status-line setup").

A runtime wins only if its session data actually resolves. Environment
variables leak into child agents, so a variable on its own proves nothing.
A resolving session id beats a runtime that only signals it is running
(`OPENCODE=1`, `PI_CODING_AGENT` or `PI_SESSION_FILE`, `CURSOR_AGENT=1`). Such a runtime is used through its cwd
fallback, and only after every session id has been tried.

If nothing resolves, `ctxcheck` exits 3. Pass `--runtime R` to skip detection.

Vendor facts below were verified on 2026-10-15. Each backend module cites its
sources.

## Support matrix

| Runtime | How it's measured | Window source | Backstop | Install command | Live-verified |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `CLAUDE_CODE_SESSION_ID` → `~/.claude/projects/*/<id>.jsonl`. Last non-sidechain assistant: `input + cache_creation_input + cache_read`. | Env, then model rules (1M or 200K) | Automatic: the skill's frontmatter `PostToolUse` hook, registered when the skill is invoked (live-verified, Claude Code 2.1.301) | none (invoking the skill registers the hook) | **Yes**, measurement and backstop (Claude Code 2.1.301) |
| Codex CLI | `CODEX_THREAD_ID` → `$CODEX_HOME/sessions/**/rollout-*-<id>.jsonl`. Last `token_count` `last_token_usage.total_tokens`. Falls back to the newest root rollout for the cwd. | The session's `model_context_window` (already ×0.95) | Opt-in: `ctxcheck install-hook codex`, then trust it in `/hooks` | `ctxcheck install-hook codex` | No: fixtures and a temp `CODEX_HOME`; `--dry-run` only against the real `hooks.json` |
| OpenCode v2 (2.0.x) | `OPENCODE_SESSION_ID` → `opencode.db` tables `session_v2` / `session_message`, opened read-only. Newest `assistant` row with `tokens` after the last completed `compaction`, before any revert point: `input + output + reasoning + cache.read + cache.write`. | `kv['models-dev:catalog']` → `limit.context`, else the v1 cache file, else a preset | Opt-in: the plugin appends the warning to the tool result after each completed tool call (`execute.after`) | `ctxcheck install-hook opencode` | No: synthetic databases; the plugin was run only under Node with stub hook objects |
| OpenCode v1 (1.19.x) | No session id; only `OPENCODE=1` is set. Newest top-level `session` whose `directory` is the cwd. Same database, tables `session` / `message`: newest assistant with `output > 0`, same five-field sum. | `$XDG_CACHE_HOME/opencode/models.json` (or `$OPENCODE_MODELS_PATH`) → `limit.context`, else a preset | Opt-in: the same plugin file, through `tool.execute.after`. The plugin passes the session id, which v1 never gives to tools. | `ctxcheck install-hook opencode` (the same file) | No: as for v2 |
| Pi (1.2.x–1.3.x) | `PI_SESSION_ID` / `PI_SESSION_FILE` → `<agent dir>/sessions/--<cwd slug>--/<stamp>_<id>.jsonl`. Walks the `parentId` chain from the last entry, so abandoned branches don't count. Latest non-aborted, non-error assistant `usage.totalTokens`, else `input + output + cacheRead + cacheWrite`. A `compaction` resets usage to 0 until the next response. | `PI_PROVIDER`/`PI_MODEL` (else the branch's model) → Pi's `models.json`, then `models-store.json`, then OpenCode's models.dev data. Otherwise `--window` / `CTXCHECK_WINDOW` / `CTXCHECK_PRESET` is required (exit 3). | Opt-in: the extension sends Pi's own `ctx.getContextUsage()` and appends the warning to the `tool_result` content | `ctxcheck install-hook pi` | No: synthetic sessions; the extension was run only under Node with a stub `pi` and `ctx` |
| Copilot CLI (1.0.98) | `COPILOT_AGENT_SESSION_ID` → the sidecar `statusline/copilot/<id>.json`, else the newest sidecar for the cwd. `context_window.current_context_tokens`, else `current_usage` `input + cache_creation_input + cache_read_input`, else `used_percentage` × window. | `context_window.displayed_context_limit`, else `context_window_size`. No preset fallback. | Opt-in: `ctxcheck install-hook copilot` (`postToolUse`; live-unverified) | `ctxcheck install-statusline copilot` + `ctxcheck install-hook copilot` | No: not installed; payloads built from the docs |
| Antigravity `agy` (1.3.x) | `ANTIGRAVITY_CONVERSATION_ID` → `statusline/antigravity/<id>.json`. Without it, `~/.gemini/antigravity-cli/cache/last_conversations.json` maps the cwd to its conversation, else the newest sidecar for the cwd. `current_usage` `input + cache_creation_input + cache_read_input`, else `used_percentage` × window. | `context_window.context_window_size`. No preset fallback. | Opt-in: `ctxcheck install-hook antigravity` (`PreInvocation`, before each model call) | `ctxcheck install-statusline antigravity` + `ctxcheck install-hook antigravity` | No: `--dry-run` only against the real config files |
| Cursor CLI | No session id; `CURSOR_AGENT=1` only says Cursor is running. The newest sidecar whose `cwd` is the cwd. `current_usage` as above (its fields are undocumented), else `used_percentage` × window. | `context_window.context_window_size`. No preset fallback. | Opt-in: `ctxcheck install-hook cursor` (`postToolUse`, matcher `Shell`; live-unverified) | `ctxcheck install-statusline cursor` + `ctxcheck install-hook cursor` | No: not installed; payloads built from community sources and docs |

The "Backstop" column records how a runtime gets the post-tool warning when an
armed session goes over its gate (see "Backstops"). Every runtime has one;
all but Claude Code's are opt-in. "Live-verified" means run inside a real
session of that runtime; everything else was tested against synthetic data
built from the vendor's docs or source.

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

**Status-line sidecar.**
- Snapshots live under
  `${CTXCHECK_HOME:-${XDG_STATE_HOME:-~/.local/state}/jelly-skills/ctxcheck}/statusline/<runtime>/<session>.json`
  as `{received_at, cwd, runtime, session, payload}`. A payload with no
  session id is filed per cwd (`cwd-<path>.json`).
- `--transcript` accepts a snapshot path; the runtime is taken from its
  directory.
- Which payload fields are confirmed differs per runtime:
  - **Antigravity:** documented at
    antigravity.google/docs/cli/statusline (`conversation_id`, `cwd`,
    `model`, `context_window.*`). `ANTIGRAVITY_CONVERSATION_ID` was found only
    in the `agy` binary.
  - **Copilot CLI:** the docs name no keys. They come from the CLI's
    bundled JavaScript (1.0.98), and the numbers are computed in native code.
    `COPILOT_AGENT_SESSION_ID` is confirmed by the changelog (1.0.29).
  - **Cursor CLI:** there are no official docs for the status line. The
    payload shape comes from copies of Cursor's built-in statusline skill
    and from forum replies by Cursor staff. `current_usage` is `null` in
    every published example.

## Status-line setup

Copilot CLI, Antigravity and Cursor CLI show context usage only to their
status-line command. Running `ctxcheck install-statusline <runtime>` once
makes that command also save a snapshot that `ctxcheck` can read:

```bash
python3 <context-budget dir>/scripts/ctxcheck install-statusline antigravity --dry-run   # show the diff
python3 <context-budget dir>/scripts/ctxcheck install-statusline antigravity
python3 <context-budget dir>/scripts/ctxcheck install-statusline antigravity --uninstall
```

**What changes.** Exactly one setting changes, `statusLine.command`, in one
file:

| Runtime | File |
| --- | --- |
| Antigravity | `~/.gemini/antigravity-cli/settings.json` |
| Copilot CLI | `$COPILOT_HOME/settings.json` (default `~/.copilot/settings.json`) |
| Cursor CLI | `$CURSOR_CONFIG_DIR/cli-config.json` (default `~/.cursor/cli-config.json`) |

How the command changes:
- An existing status-line command is wrapped. It becomes
  `python3 <abs ctxcheck> statusline-tee --runtime R -- '<original>'`.
- The tee saves the snapshot and then runs the original through the shell
  with the same input. Your status line looks exactly as before.
- With no previous status line, the tee prints `ctx NN%`.
- Every other byte of the file stays as it was.
- Before writing, the file is copied to
  `<file>.ctxcheck-<UTC timestamp>.bak`.
- Running the installer again changes nothing. If the skill has moved, it
  updates the `ctxcheck` path instead.

Limits:
- Files that aren't strict JSON (comments, trailing commas) are refused.
- Cursor's config file must already exist; run Cursor once first.
- Install while no Cursor session is running. An exiting session can
  rewrite `cli-config.json`.
- Restart Copilot CLI after installing.

**How to undo.** `--uninstall` puts the original command back in place. If
there was no original, it removes what the installer added. It does not copy
the backup over the file, so other settings changed since the install are
kept. The backups stay where they are until you delete them.

**Staleness.** A snapshot updates only while the interactive TUI redraws its
status line:
- Antigravity redraws when the agent state changes.
- Copilot redraws on state changes, or every `refreshInterval` seconds if
  that is set.
- Cursor redraws on conversation updates.

`ctxcheck` reports the snapshot's age as `stale_seconds`. It refuses
snapshots older than 900 seconds and exits 3 with "status line hasn't
refreshed". Non-interactive (headless) runs draw no status line,
so they can't be measured.

**Copilot CLI isn't an Orca worker.** Orca has no Copilot agent id for
dispatched workers. In a run-plan program, Copilot can only be the
coordinator or a hand-run planner session.

## Backstops

A backstop is a post-tool hook that runs `ctxcheck --hook`. It stays silent
unless the session is armed (`ctxcheck --arm`) and over its gate. Then it adds
one role-specific instruction to the model's context, and it repeats the
warning only after usage climbs another 5 points. Every error exits 0 and
prints only the runtime's silent answer (nothing for Claude Code and Codex,
`{}` for the others), so a broken backstop never blocks a tool call.

**Claude Code: automatic.**
- The `context-budget` SKILL.md frontmatter declares a `PostToolUse` hook
  (matcher `Bash`). Claude Code registers it when the skill is invoked and
  keeps it for the rest of the session.
- Its command first checks the markers dir, so an unarmed machine costs one
  `ls`. It then finds `ctxcheck` through `$CLAUDE_PLUGIN_ROOT`, which Claude
  Code 2.1.301 sets to the skill's dir for skill hooks (observed, not
  documented). If that fails it searches
  `$CLAUDE_PROJECT_DIR/.claude/skills/context-budget`,
  `~/.claude/skills/context-budget` and `~/.agents/skills/context-budget`.
- `${CLAUDE_SKILL_DIR}` is *not* expanded in hook commands.
- Live-verified 2026-10-15: the hook fired on a later turn, the skill's
  copy of `ctxcheck` was found, and the warning reached the model.

**Codex CLI: `ctxcheck install-hook codex [--dry-run|--uninstall]`.**
- **What changes:** the installer appends one group to
  `$CODEX_HOME/hooks.json` (default `~/.codex`), under `hooks.PostToolUse`:
  `{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3
  <abs ctxcheck> --hook --runtime codex", "timeout": 10}]}`.
- It appends after existing groups, such as Orca's. Codex keys hook trust by
  position, so existing trust stays valid.
- It makes a timestamped backup first. Re-running is a no-op. `--dry-run`
  prints the diff and writes nothing.
- **Trust:** Codex skips a new hook until you trust it. Start Codex, run
  `/hooks`, and trust the ctxcheck entry.
- **Undo:** `--uninstall` removes only the ctxcheck group and leaves every
  other byte as it was.
- **Codex tolerates the skill's frontmatter:** Codex 0.162.1 ignores the
  frontmatter `hooks:` key. Its `SkillFrontmatter` has no
  `deny_unknown_fields`; see `codex-rs/skills/src/parser.rs`.
- The Codex hook is tested against a temp `CODEX_HOME`. It is not
  live-verified in a Codex session.

**Cursor, Copilot CLI and Antigravity: `ctxcheck install-hook R`.**
- These three are measured from the status-line sidecar, so their backstop
  needs `install-statusline R` too. The hook reads the sidecar for the
  payload's session id. It never reads the payload's transcript path, which
  points at the runtime's own transcript.
- **Cursor** (`postToolUse`): the payload's `conversation_id` names the
  session. `session_id` is accepted too; the docs call it "same as
  `conversation_id`". The warning goes out as
  `{"additional_context": "..."}`, which Cursor injects after the tool
  result.
- **Copilot CLI** (`postToolUse`): the casing of the event key sets the
  casing of the payload. Both are read: camelCase `sessionId`, and the
  snake_case `session_id` sent for a PascalCase `PostToolUse` key. The
  warning goes out as `{"additionalContext": "..."}`, which Copilot appends
  to the tool result (capped at 10 KB).
- **Antigravity** (`PreInvocation`): `PostToolUse` must return `{}`, so the
  warning rides `PreInvocation`, before each model call. It goes out as
  `{"injectSteps": [{"ephemeralMessage": "..."}]}`, a transient system
  message. Pre- and PostInvocation payloads look identical, so the installed
  command passes `--event PreInvocation`, and every other event gets `{}`.
  The session id is `conversationId`.
- When silent, all three print `{}`: their contracts parse stdout as JSON on
  exit 0.
- **Live-unverified.** Cursor and Copilot weren't installed when this was
  written, so their hooks were tested only against payloads built from their
  docs. Antigravity's contract comes from its bundled docs (agy 1.3.2). Only
  `--dry-run` was run against the real `~/.gemini/config/hooks.json`, and it
  left the file unchanged (sha256 checked).

**OpenCode and Pi: `ctxcheck install-hook opencode|pi`.**
- Neither runtime runs shell hooks, so the installer copies a small
  TypeScript file from `scripts/assets/`, with this ctxcheck's absolute
  path filled in. The file needs no dependencies and no build step: OpenCode
  and Pi load `.ts` directly. After each tool call it runs
  `python3 <abs ctxcheck> --hook --runtime R`. It sends JSON on stdin, waits
  at most 5 seconds, and appends the reply's `message`, if any, to the tool
  result. Every error is swallowed.
- **OpenCode** (v1 and v2 from one file): the default export carries `id`,
  a v2 `setup()` that registers `ctx.tool.hook("execute.after")`, and a v1
  `server()` that returns `"tool.execute.after"`. v1 needs 1.18.29 or later.
  It sends `{runtime, session_id}`, and ctxcheck reads usage from
  `opencode.db`. v2 replaces `event.result` with the original content plus a
  text part. v1 appends to `output.output`, or to an MCP tool's `content`.
- **Pi** (`tool_result`): it sends `{runtime, session_id, used, window}`
  from `ctx.getContextUsage()` and `ctx.sessionManager.getSessionId()`.
  ctxcheck trusts these numbers and reads no session file. The marker's
  window still wins over the payload's. Right after a compaction Pi reports
  `tokens: null`, and the extension then sends nothing. The extension returns
  `{content: [...original, warning]}` and keeps any `structuredContent`.
- `--hook` replies `{"message": "..."}`, or `{}` when silent.
- **Live-unverified.** Neither runtime was installed when this was written.
  Both files were checked against the source cited in them (OpenCode v1.19.1
  and v2.0.30, Pi v1.3.0) and run under Node 26 with stub hook objects against
  a real `ctxcheck` in a temp home. `--dry-run` against the real
  `~/.pi/agent/extensions` and `~/.config/opencode/plugins` left both
  unchanged (sha256 checked).

## Installing backstops

Every installer takes `--dry-run` or `--uninstall`. `--dry-run` prints the
diff and writes nothing. Before writing, each installer copies the file to
`<file>.ctxcheck-<UTC stamp>.bak`, then replaces it atomically.

- Re-running is a no-op. An old ctxcheck path in the command is updated in
  place.
- Uninstall removes only what install added. Every foreign entry (Orca's,
  the user's) stays byte-identical.
- A file that isn't strict JSON (comments, trailing commas) is refused.
- OpenCode and Pi are different: the installer writes a whole file of its
  own, `ctxcheck-context-budget.ts`. No backup is made, since the file is
  regenerated on every install. The first line marks it as ctxcheck's, and a
  file of that name without the mark is refused. Uninstall deletes only that
  file, and every other file in the directory is left alone. Pi's
  extensions directory also holds Orca's extensions.

| Runtime | Command | File touched | What install adds | Undo | Trust prompt |
| --- | --- | --- | --- | --- | --- |
| Claude Code | none | none | The skill's frontmatter hook, registered when the skill is invoked | The session ends | None |
| Codex CLI | `ctxcheck install-hook codex` | `$CODEX_HOME/hooks.json` (default `~/.codex`) | Appends a `hooks.PostToolUse` group: matcher `Bash`, `{"type": "command", "command": "python3 <abs ctxcheck> --hook --runtime codex", "timeout": 10}` | `--uninstall` | **Yes:** run `/hooks` in Codex and trust the entry |
| Cursor CLI | `ctxcheck install-hook cursor` | `~/.cursor/hooks.json`, created as `{"version": 1, ...}` when missing | Appends to `hooks.postToolUse`: `{"command": "python3 <abs ctxcheck> --hook --runtime cursor", "matcher": "Shell", "timeout": 10}` | `--uninstall`. A file it created keeps `{"version": 1}`. | None for user hooks. Cursor reloads the file on save. |
| Copilot CLI | `ctxcheck install-hook copilot` | `$COPILOT_HOME/hooks/ctxcheck.json` (default `~/.copilot`), a file of its own | `{"version": 1, "hooks": {"postToolUse": [{"type": "command", "bash": "python3 <abs ctxcheck> --hook --runtime copilot", "timeoutSec": 10}]}}`, with no matcher | `--uninstall`. Once nothing else is in the file, it is moved aside to `<file>.ctxcheck-<UTC stamp>.bak` rather than deleted. | None for user hooks ("workspace admission" is for repo hooks). Restart Copilot. |
| OpenCode (v1, v2) | `ctxcheck install-hook opencode` | `${XDG_CONFIG_HOME:-~/.config}/opencode/plugins/ctxcheck-context-budget.ts` (auto-loaded, no `opencode.json` entry) | The whole file, from `scripts/assets/opencode-plugin.ts` | `--uninstall` deletes the file | None. Restart OpenCode. |
| Pi | `ctxcheck install-hook pi` | `<agent dir>/extensions/ctxcheck-context-budget.ts` (`$PI_CODING_AGENT_DIR`, default `~/.pi/agent`; auto-loaded) | The whole file, from `scripts/assets/pi-extension.ts` | `--uninstall` deletes the file | None. Start a new session or `/reload`. |
| Antigravity `agy` | `ctxcheck install-hook antigravity` | `~/.gemini/config/hooks.json`, the global customization root | A top-level hook named `ctxcheck`: `{"PreInvocation": [{"type": "command", "command": "python3 <abs ctxcheck> --hook --runtime antigravity --event PreInvocation", "timeout": 10}]}` | `--uninstall`. A foreign hook named `ctxcheck` is refused. `"enabled": false` is kept. | None documented. Start a new `agy` session. |

The backstop stays silent until the session is armed (`ctxcheck --arm`). On
the three status-line runtimes, keep the TUI on screen so the sidecar stays
fresh.

## Known gaps

- **OpenCode window.** Custom providers configured only in `opencode.json`
  aren't in the models.dev catalog. Their window falls back to a preset, so
  pass `--window` for them.
- **OpenCode v1.** With two OpenCode sessions open in one directory, the cwd
  fallback picks the most recently updated one.
- **OpenCode and Pi backstops.** They are live-unverified. The installed
  file holds an absolute ctxcheck path, so re-run `install-hook` after
  moving the skill. OpenCode v1 before 1.18.29 can't load the plugin's
  object export.
- **Pi.**
  - Pi itself shows the count as unknown after a compaction. `ctxcheck`
    reports 0 until the next response.
  - A `context_edit` is treated the same way.
- **Status-line runtimes.**
  - Copilot and Cursor weren't installed when this was written. Their
    readers and backstops were tested against payloads built from the
    sources above, not against a live session.
  - The backstop finds the sidecar by the hook payload's session id, which
    is assumed to match the status line's id. Cursor documents this
    (`session_id` is "same as `conversation_id`"). For Copilot and
    Antigravity it is unconfirmed. If they differ, the hook stays silent.
  - Cursor has no session id. With two Cursor sessions in one directory, the
    most recently refreshed one is used.
  - The snapshot is only as fresh as the last redraw. A long tool call can
    change usage without a redraw.
