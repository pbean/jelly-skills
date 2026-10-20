# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and each skill is versioned independently in its `SKILL.md` metadata.

## [Unreleased]

### run-plan 1.4.0

A second dispatch backend, herdr, alongside Orca. Checked against herdr 0.9.5's
group listings and docs, but not yet live-validated: the manual smoke test in
`tests/e2e/README.md` (section 6) is its first real run.

#### Added

- `references/dispatch-herdr.md`: run-plan's herdr backend. Each leg attempt
  runs in its own unfocused tab, started readiness-aware with `herdr agent
  start`. The prompt is delivered as one line with `herdr agent prompt --wait`,
  whose `working` return proves the turn started. The coordinator waits on the
  agent's lifecycle state with rolling `herdr agent wait`s and reads a
  per-attempt result file (or ask file) as the completion report (or
  escalation). Release closes the attempt's tab. Coordinator succession opens a
  successor tab and confirms the takeover from `run-state.json`, since herdr has
  no message bus, task DAG or run fencing.
- Optional run-config key `backend` (`orca` | `herdr`). When it is absent, the
  runner detects it (an Orca terminal handle means `orca`, `HERDR_ENV=1` means
  `herdr`), asks once if both or neither apply, and writes the answer into the
  plan and `run-state.json`.
- Optional run-config key `agent_args` (herdr only): a string passed verbatim to
  the worker agent after the model and effort, typically permission flags such
  as `--dangerously-skip-permissions` for an unattended `claude`.
- Doc tests: every dispatch file has the same `## 0.`-`## 9.` / `### 8a.` /
  `### 8b.` section shape; SKILL.md's body names no backend outside "Dispatch
  backends"; every `herdr <group> <verb>` and its flags in `dispatch-herdr.md`
  exist in the installed herdr's usage lines (skipped without herdr).

#### Changed

- SKILL.md loads `references/dispatch-<backend>.md` and describes its loop and
  failure machine in backend-neutral vocabulary ("dispatch §N", leg record,
  start receipt, completion report, escalation, release, retry on the same leg),
  which each dispatch file defines. "Swapping the dispatch backend" is now
  "Dispatch backends", with one row per backend and the section contract.
- `coordinator-handoff.md`'s inbox steps (settle, stop acknowledging, process
  the replayed batch) apply only when the backend has an inbox, and the takeover
  is confirmed by `run-state.json` alone when the backend has no fencing.
- The handoff and SKILL.md neutrality checks now also reject herdr names.
- `dispatch-orca.md`'s opening no longer claims to be the only backend file.
  Orca behavior is unchanged.

#### Fixed

- The successor's `shell_command` reaches `herdr pane run` and Orca's
  `terminal create --command` through `with-shell-command.py` (handoff step 6)
  as one argv element. Pasted inside `"..."`, the predecessor's shell expanded
  `$(...)` or backticks that `shlex.join` had single-quoted.
- `<runs dir>` is shell-safe by construction (SKILL.md Step 0): the plan
  basename is reduced to `A-Z a-z 0-9 . _ -`, and a plan directory holding `$`,
  a backtick, `"`, `%` or `!` falls back to `~/.agents/plans/runs/`. Workers
  paste runs-dir paths inside `"..."`, where those characters still expand. The
  herdr result rename now uses a fixed `<result path>.tmp` temp name.
- Two plans no longer share a runs directory. Before, they did when their
  basenames sanitized alike, or when same-named plans sat in different repos.
  `run-state.json` now records `plan_path`. A sanitized name, or one already
  used by another plan, gets an 8-hex SHA-256 suffix of the plan path. An
  existing run under the old unsanitized name is still resumed from there.
- herdr: a worker whose result file appears while it is still `working` or
  `blocked` is waited to `idle` or `done` before release, so the coordinator
  never closes a pane mid-turn (`agent_prompt_stalled` and `timeout` in §5 no
  longer jump straight to §7).

### run-plan 1.3.0

Checked against Orca 1.4.229, whose `orca-cli` and `orchestration` skills are now
stubs that load a version-matched guide from the binary.

#### Added

- Optional run-config key `effort`, passed to `worker-start --effort` together
  with `model`.
- A lost `worker-start` or `task-create` response is recovered with
  `orchestration request-show` and `--retry-request` instead of being re-run.
- The coordinator reads `worker-show`'s `observation.agentWait` to spot a worker
  parked on a permission or trust prompt, at first contact and during long waits.

#### Changed

- §0 loads the compact orchestration guide and fetches the recovery reference
  with `--reference` only when needed, instead of `--full` up front.
- Orca's three-strike breaker counts only failed starts, not a worker's own
  `--outcome failed`. `max_consecutive_failures` is documented as the bound on
  worker-reported failures, and a circuit-broken retry blocks the phase.
- The first-contact heartbeat copies the preamble's command verbatim; newer
  preambles carry no dispatch capability.
- `--model` notes cover omp and opencode (existing worktree) per 1.4.229.
- Task-list reconciliation uses `task-list --brief`, and the reclaimable sweep
  follows `page.nextCursor`.

#### Fixed

- A successor coordinator no longer checks its budget at the boundary it resumes
  at. Its predecessor already checked it, so a successor whose takeover alone
  crossed the gate handed off again before dispatching anything, and so did the
  next one.

### Tests

- The live smoke kit's fixture plan has three phases, so a run now covers two
  successions (generation 1 to 2 mid-phase, 2 to 3 at a phase boundary) and a
  last generation that finishes instead of handing off. Its README covers
  Claude Code's folder-trust prompt and records plain `run-use` fencing as
  verified against Orca 1.4.229.

### session-plan 1.1.2

- The plan template documents the optional `backend` and `agent_args`
  run-config keys, and that `agent` is a herdr `--kind` under herdr.

### context-budget 1.1.1

- `runtimes.md` and `worker.md`: Copilot CLI can't be a phase worker under
  Orca, but it can under run-plan's herdr backend, which has a `copilot` kind.
- `ctxcheck launch-command` exits 3 on native Windows with a message saying so,
  instead of reporting no agent ancestor after `ps` fails. WSL is unaffected.

### session-plan 1.1.1

- The plan template documents the optional `effort` run-config key.

### context-budget 1.1.0

#### Changed

- The default gate on windows of 600K or more is now 25% for every role
  (~250K used on a 1M window), down from 28% for workers and planners and 26%
  for the coordinator. Windows of 150K-600K stay at 75%, smaller ones at 80%.

#### Added

- `${CTXCHECK_HOME:-${XDG_STATE_HOME:-~/.local/state}/jelly-skills/ctxcheck}/bands.json`
  replaces the default role bands for every session. `ctxcheck --list-presets`
  shows which bands are in effect, and a malformed file exits 1.

## 2026-10-15 — context-budget 1.0.0, run-plan 1.2.0, session-plan 1.1.0

The context-budget program: agent sessions measure their own context, phase
workers and the planner hand off before they degrade, and the run-plan
coordinator hands off to a successor coordinator.

### context-budget 1.0.0

First release of the new skill.

#### Added

- The `context-budget` skill with `ctxcheck`, built on the `ctxlib` package.
  - It reads Claude Code and Codex CLI sessions.
  - It derives per-role handoff gates (coordinator, worker, planner) from the
    window.
  - It exits 3 when the runtime can't be measured.
  - Codex usage is `last_token_usage.total_tokens`, which matches the
    TUI.
  - The Claude window comes from env and model rules.
  - Includes a stdlib `unittest` suite under `tests/`.
- OpenCode (v1 and v2) and Pi backends to `ctxcheck`.
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
- A status-line sidecar for Copilot CLI, Antigravity and Cursor CLI,
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
- Arming and a post-tool backstop for Claude Code and Codex CLI.
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
- Opt-in backstops for Cursor CLI, Copilot CLI and Antigravity.
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
- Opt-in backstops for OpenCode (v1 and v2) and Pi.
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
- The worker and planner doctrine.
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
- `ctxcheck launch-command [--json]`. It prints the command line that
  starts a fresh session of the agent CLI running this one. It walks the
  process ancestry (Linux `/proc`, macOS `ps`) to the nearest Claude Code,
  Codex, OpenCode, Pi, Cursor, Copilot or Antigravity process, run directly
  or through node, bun or deno (the outermost of a loader and its native
  child). It strips session-resume arguments and the initial prompt per
  runtime, from each CLI's verified flags, and keeps the launch posture.
  Output is `{runtime, argv, shell_command, cwd, pid, stripped}`. It exits
  3 when no ancestor is an agent CLI. `tests/test_launch.py` covers the
  stripping per runtime and the walk over an injected process table.

### run-plan 1.2.0

#### Changed

- Modernized the Orca dispatch for Orca 1.4.219+, and phase continuations
  now run as new tasks.
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
#### Added

- The coordinator measures itself and hands off to a successor
  automatically.
  - SKILL.md has a "Context budget" section. The coordinator invokes
    context-budget (Claude Code), arms as `--role coordinator` (with
    `coordinator_handoff_pct` as `--gate` when set), checks at every boundary
    with no worker in flight (before the next phase goes `[running]`, and
    between continuation legs), and disarms on every exit. Step 0 routes a
    pending takeover to the successor branch. run-state.json gains
    `coordinator_generation`, `takeover_pending`, `takeover` and
    `handoffs[]`, and the Exit states table a "Handed off" row.
  - New `references/coordinator-handoff.md`, backend-neutral. The
    predecessor finishes the last phase rather than hand off for it; settles
    its inbox; writes a handoff note (phases, legs mid-continuation, failure
    counters, regenerated phase files, unverified facts); records the
    handoff atomically before launching; never acknowledges a delivery after
    the note; disarms; launches the successor with its own detected command
    line and one line of prompt; and confirms the takeover by being fenced or
    by the successor's `takeover` record. On a dialog it tells the user and
    doesn't re-send; on a swallowed line it re-sends once; otherwise it
    re-arms, clears the pending takeover and asks the user. Outside a backend
    terminal it stops and leaves the note for a manual successor. The
    successor rebinds the run, acknowledges the takeover, skips settled
    confirmations, reconciles with recorded state winning, arms, and resumes.
  - `references/dispatch-orca.md` §9 "Coordinator succession" holds the Orca
    commands: the `$ORCA_TERMINAL_HANDLE` + `terminal show` probe, `terminal
    create --worktree active --command`, `terminal wait --for tui-idle`
    (`wait.satisfied`), `terminal send --wait-submit 30` (`turn_started` in
    `send.prompt.stages`, `--retry-request` on a timeout), the
    `check --wait --timeout-ms 600000` takeover wait, and one bounded
    `terminal read --screen`. Every `terminal` verb names its terminal
    explicitly.
  - `tests/test_docs.py` checks that the handoff doctrine names no Orca
    command, that §9's verbs exist and name their terminal, and that every
    ctxcheck call in run-plan's docs is a real one.

### session-plan 1.1.0

#### Added

- The planner measures itself. It invokes context-budget at the
  start (Claude Code), arms as `--role planner` as its first action (allowed
  in plan mode: ctxcheck writes only its own state dir), checks after
  exploration, after the design settles and before writing session prompts,
  saves and stops when over budget, resumes from a planning continuation
  without re-asking settled decisions, and disarms after the handoff.

### Repository

#### Added

- `tests/`: a stdlib `unittest` suite (no third-party dependencies) with
  synthetic fixtures, run with `python3 -m unittest discover -s tests -v`.
- `tests/e2e/`: a live smoke-test kit. `smoke-plan.md` is a 2-phase dummy
  plan with both handoff gates at 1%, and `README.md` walks through running
  it in a scratch repo: one continuation leg as a new task, one coordinator
  succession, and cleanup.

#### Changed

- README: context-budget in the skills table and the agent support table;
  install all three skills together; new "Context budgets" section.
