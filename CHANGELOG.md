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
- Added the worker and planner doctrine (0.2.0).
  - The SKILL.md body covers roles and who arms which, arming at the start,
    checks at boundaries only, exit 3 ("no context budget on this runtime"),
    disarming on every exit, the backstop per runtime, and the gate table.
  - `references/worker.md`: the over-budget procedure for a phase worker. It
    stops at a safe point, leaves edits uncommitted, saves a
    `git add -A -N && git diff` patch backup, writes (or appends to) the
    continuation note, disarms, and reports `PHASE-STATUS:
    continuation-required` with outcome succeeded.
  - `references/planner.md`: save and stop. The draft goes to the plan file
    and a planning continuation to `<plan>.planning-continuation.md` (a final
    `## Planning continuation` section under Claude plan mode). The user gets
    one line to resume in a fresh session.
  - `tests/test_docs.py` checks that every `ctxcheck` flag and subcommand
    named in `skills/**/*.md` exists, that each SKILL.md frontmatter has the
    required keys, and that relative links in SKILL.md files resolve.

### run-plan

- Modernized the Orca dispatch for Orca 1.4.219+, and phase continuations
  now run as new tasks (1.1.0).
  - `references/dispatch-orca.md` §§0-8 were rewritten. A phase runs as legs,
    and each leg is its own task, created lazily just before its dispatch
    (nothing is pre-built as a chain). The start receipt's `turnStart` drives
    first contact: `observed` skips the heartbeat wait, and only `permission`
    or `unsupported` keep the heartbeat, one look and one re-delivery.
    `outcome_unknown` gets inspected and settled explicitly. Waits use
    `--timeout-ms 900000`; after three empty waits the coordinator runs
    `worker-list --include-remote` and acts only on positive evidence.
    Workers are read with `worker-read --source auto`, never `terminal read`.
    A failed attempt retries on the same task with `--retry-of` and restated
    placement, and the run ends only once no reclaimable worker is left. It
    also documents that a plain `run-use` from another terminal rebinds the
    Run and fences the previous coordinator.
  - The `worker_done` body puts `PHASE-STATUS:` on its first line, then the
    three-sentence summary. Workers check for coordinator follow-ups at
    checkpoints and before `worker_done`.
  - `references/plan-format.md` gained the optional run-config keys
    `worker_handoff_pct` and `coordinator_handoff_pct`. The wrapper suffix
    now has workers measure themselves: invoke context-budget on Claude Code,
    arm as `--role worker`, check after each numbered step, follow
    context-budget's `references/worker.md` when over budget (with a
    per-leg patch backup), and disarm on both exits. The runner resolves
    `<ABS_CTXCHECK>` once at run start from the sibling skill, and runs
    without budgets (after one warning) if that skill is missing.
  - SKILL.md: run-state.json records per-phase `legs` and `ctxcheck_path`,
    and every write to it is atomic. The loop, failure machine and resume
    work in terms of legs.
  - `tests/test_docs.py` checks that every `orchestration` and `terminal`
    verb named in run-plan's docs is listed, and not retired, in
    `orca-ide`'s help. It skips that check when `orca-ide` is absent. It
    also checks the wrapper's ctxcheck flags.

### session-plan

- The planner measures itself (1.1.0). It invokes context-budget at the
  start (Claude Code), arms as `--role planner` as its first action (allowed
  in plan mode: ctxcheck writes only its own state dir), checks after
  exploration, after the design settles and before writing session prompts,
  saves and stops when over budget, resumes from a planning continuation
  without re-asking settled decisions, and disarms after the handoff.

### README

- Added context-budget to the skills table; install all three skills
  together.
