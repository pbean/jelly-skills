# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and each skill is versioned independently in its `SKILL.md` metadata.

## [Unreleased]

### context-budget

- Added the `context-budget` skill (0.1.0) with `ctxcheck`, built on the `ctxlib` package.
  - It reads Claude Code and Codex CLI sessions.
  - It derives per-role handoff gates (coordinator, worker, planner) from the
    window.
  - It exits 3 when the runtime can't be measured.
  - Codex usage is `last_token_usage.total_tokens`, which matches the
    TUI.
  - The Claude window comes from env and model rules.
  - Includes a stdlib `unittest` suite under `tests/`.
- Added OpenCode (v1 and v2) and Pi backends to `ctxcheck`.
  - OpenCode reads `opencode.db` read-only. On v2 it skips usage before the
    last completed compaction. On v1, which sets no session id, it uses the
    newest session for the cwd. The window comes from OpenCode's models.dev
    data, then a preset.
  - Pi walks the session tree from the leaf and ignores abandoned branches,
    pre-compaction usage, and aborted or errored replies. The window comes
    from Pi's model files or OpenCode's models.dev data. If neither has it,
    ctxcheck exits 3 and asks for `--window` or `CTXCHECK_WINDOW`.
  - A resolving session id now beats a runtime that is only flagged as
    present (`OPENCODE=1`, `PI_CODING_AGENT`).
  - Added `references/runtimes.md` with the support matrix.
- Added a status-line sidecar for Copilot CLI, Antigravity and Cursor CLI,
  which keep their context count in memory only.
  - `ctxcheck statusline-tee --runtime R [-- cmd]` saves each status-line
    payload as an atomic snapshot. It then runs the original command and
    passes its output and exit code through, even when saving the snapshot
    fails.
  - `ctxcheck install-statusline cursor|copilot|antigravity
    [--dry-run|--uninstall]` wraps the runtime's status-line command. It
    edits only that one value, makes a timestamped backup, and is
    idempotent. `--uninstall` puts the original back.
  - Readers report `stale_seconds` and `measure_source: statusline`. They
    refuse snapshots older than 15 minutes (exit 3).
- Added arming and a post-tool backstop for Claude Code and Codex CLI.
  - `ctxcheck --arm --role R --label "..." [--gate PCT] [--window N]`
    records the session's role, gate (derived per role unless given) and
    measured window in `markers/<session>.json`. `--disarm` removes it.
    Arming reaps markers untouched for 7 days.
  - A plain `ctxcheck` in an armed session uses the marker's role, gate and
    window.
  - `ctxcheck --hook [--runtime R]` warns an armed session that is over its
    gate, with a role-specific instruction (worker, coordinator, planner). It
    re-warns only after 5 more points and always exits 0.
  - Claude Code: the skill's frontmatter `PostToolUse` hook registers when the
    skill is invoked. This was verified live, including on later turns.
  - Codex: `ctxcheck install-hook codex [--dry-run|--uninstall]` appends a
    `PostToolUse` group to `$CODEX_HOME/hooks.json`. Uninstall leaves every
    other entry byte-identical; the hook must be trusted in `/hooks`.
- Added opt-in backstops for Cursor CLI, Copilot CLI and Antigravity.
  - `ctxcheck install-hook cursor|copilot|antigravity [--dry-run|--uninstall]`
    edits `~/.cursor/hooks.json` (`postToolUse`), creates
    `$COPILOT_HOME/hooks/ctxcheck.json` (`postToolUse`), or adds a hook named
    `ctxcheck` to `~/.gemini/config/hooks.json` (`PreInvocation`). Each
    merges into the existing file, is idempotent, and leaves foreign entries
    byte-identical on uninstall.
  - `--hook` reads each runtime's payload (Copilot in both casings) and
    answers in its shape: Cursor `additional_context`, Copilot
    `additionalContext`, Antigravity `injectSteps` `ephemeralMessage`. It
    prints `{}` when silent. Antigravity warns only on `PreInvocation`
    (`--event`), because `PostToolUse` must return `{}`.
  - These are built from the documented contracts and are not live-verified.
- Added opt-in backstops for OpenCode (v1 and v2) and Pi.
  - `ctxcheck install-hook opencode|pi [--dry-run|--uninstall]` copies a
    dependency-free TypeScript file (`scripts/assets/`) into
    `~/.config/opencode/plugins/` or `~/.pi/agent/extensions/` as
    `ctxcheck-context-budget.ts`, with the absolute ctxcheck path filled in.
    It is idempotent. Uninstall deletes only that file, and a foreign file
    of the same name is refused.
  - One OpenCode file serves v2 (`setup()`, `tool.hook("execute.after")`)
    and v1 (`server()`, `"tool.execute.after"`). The Pi extension sends
    `ctx.getContextUsage()` on each `tool_result`. Both append the warning
    to the tool result, wait at most 5 s, and swallow every error.
  - `--hook` accepts `{runtime, session_id, used?, window?}` from them. It
    trusts supplied usage instead of reading the session file, and answers
    `{"message": ...}` or `{}`.
  - `references/runtimes.md` now lists the install command and
    live-verification state for every runtime. These two are not
    live-verified.
