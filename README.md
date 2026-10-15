# jelly-skills

Three companion [Agent Skills](https://agentskills.io) for running large changes as a series
of fresh agent sessions, installable with the [`skills` CLI](https://github.com/vercel-labs/skills).

| Skill | What it does |
| --- | --- |
| **session-plan** | Plans a feature, refactor or migration as a *session program*: phases each sized to fit one fresh session, a ready-to-paste kickoff prompt per phase, everything landing on one branch as one PR. It stops after the plan is approved and hands off instead of implementing. Small tasks get a one-session fast path. |
| **run-plan** | Runs a session-plan plan unattended: dispatches each pending phase to a fresh agent session through Orca orchestration, waits for it, checks the plan file was updated, and moves on. It stops when the plan is done or a phase is blocked. A phase that outgrows one session continues in a new one, and a coordinator that fills its own context hands the run to a successor coordinator. |
| **context-budget** | Measures how full an agent session's context window is (`ctxcheck`, Python 3.9+ stdlib) against a per-role handoff gate, and holds the doctrine for what to do when a session goes over: a phase worker hands the rest of its phase to a fresh worker, a planner saves its draft and stops. Reads Claude Code, Codex, OpenCode and Pi sessions directly, and Copilot CLI, Antigravity and Cursor CLI through an opt-in status-line sidecar. |

The flow is: ask your agent to plan something (session-plan triggers), approve the plan,
then open a fresh session and run the plan (`/run-plan <abs plan path>` in Claude Code, or
"run the plan at `<path>`" elsewhere). Each phase also works by hand: paste its prompt into
a new session.

## Install

Install **all three skills together**. session-plan reads run-plan's
`references/plan-format.md` through the sibling path `../run-plan/`, and both
reach `ctxcheck` through the sibling path `../context-budget/`.

```bash
# from GitHub, once published
npx skills add pbean/jelly-skills --skill '*'

# from a local checkout
npx skills add ./jelly-skills --skill '*'

# list without installing
npx skills add ./jelly-skills --list

# user-level (all projects), for specific agents, no prompts
npx skills add ./jelly-skills --skill '*' -g -a claude-code -a codex -y
```

Without `-g`, skills install into the current project (`.claude/skills/` for Claude Code,
`.agents/skills/` for Codex, Cursor, OpenCode and others). Repeat `-a` per agent: the ids
include `claude-code`, `codex`, `opencode`, `pi`, `cursor`, `github-copilot` and
`antigravity`, and the CLI prints the full list if you pass an id it doesn't know. Re-run
the same command after pulling changes; running agent sessions keep the skills they loaded
until you start a new one.

### Prerequisites

- `git`; `gh` is optional and only needed when a plan uses GitHub issues as anchors.
- `python3` 3.9 or newer for `ctxcheck` (stdlib only, so macOS's system Python works).
  Docs always invoke it as `python3 <path>/ctxcheck`, because an install can lose the
  exec bit.
- run-plan needs the **Orca CLI** with orchestration. On Linux, call it as `orca-ide`.
  Never call bare `orca` there: that runs the GNOME screen reader.

## Agent support

All three skills are agent-neutral. They use these features when the agent has them, and fall
back otherwise:

| Capability | Used for | Fallback |
| --- | --- | --- |
| Plan mode (Claude Code) | Read-only planning, approval gate | Self-imposed: no edits but the plan file; wait for explicit approval |
| Structured questions (e.g. AskUserQuestion) | Settling design decisions | Ask in chat |
| Plan directory (`~/.claude/plans/`) | Where the plan file lives | `~/.agents/plans/<slug>.md` |
| Subagents | Research / exploration in phase sessions | Inline |
| Persistent memory | A program-memory anchor | Skipped; plan file (+ issue) anchors only |
| Slash commands | `/run-plan <path>` | Ask the agent to "run the plan at `<path>`" |
| Context measurement (context-budget) | Planner, worker and coordinator handoffs before a session degrades. Claude Code, Codex, OpenCode and Pi are read from their own session data; Copilot CLI, Antigravity and Cursor CLI need the status-line sidecar. | Exit 3: the session says it has no budget and carries on unmeasured |
| Context backstop hook | A warning injected after a tool call (Antigravity: before a model call) once an armed session is over its gate. Automatic on Claude Code; one opt-in installer per other runtime. | Boundary checks only: the session checks itself between steps |

The plan's `run-config` block chooses which agent the run-plan **workers** are, using any
Orca agent id (`claude`, `codex`, `cursor`, `opencode`, …). `model` is optional; leave it
out for agents that read the model from their own config.

Run state goes to `<plan dir>/runs/<plan-name>/`. If the plan file is inside a git repo,
it goes to `~/.agents/plans/runs/<plan-name>/` instead.

## Context budgets

A session that fills its context window doesn't stop. It degrades: it re-reads files,
forgets what already landed, and reports work it can no longer vouch for. context-budget
lets the three roles in a program measure themselves and hand off first:

- **Phase workers** (dispatched by run-plan) arm at the start, check after each numbered
  step, and when over their gate stop at a safe point, leave their edits uncommitted, write
  a continuation note and report `PHASE-STATUS: continuation-required`. run-plan then
  starts the next **leg** of the same phase as a new Task in the same worktree.
- **The planner** (session-plan) checks at three boundaries. When over, it saves the draft
  and a planning continuation, then tells you how to resume in a fresh session.
- **The run-plan coordinator** checks between phases and between legs. When over (and
  more than the last phase is left), it writes a handoff note, launches a successor
  coordinator in a new Orca terminal, confirms the takeover and stops. The successor
  rebinds the Run and carries on. Each coordinator is a **generation**.

Hand-pasted sessions aren't measured unless you ask (`ctxcheck` on its own just reports).

### What is measured

| Runtime | Used tokens from | Window from | Backstop |
| --- | --- | --- | --- |
| Claude Code | Its session transcript (`~/.claude/projects/`) | Env (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_DISABLE_1M_CONTEXT`), then model rules (1M or 200K) | **Automatic**: the skill's frontmatter `PostToolUse` hook, registered when the skill is invoked |
| Codex CLI | Its rollout file (`last_token_usage.total_tokens`, matching the TUI) | The rollout's `model_context_window` | `install-hook codex`, then trust it in `/hooks` |
| OpenCode v1 and v2 | `opencode.db`, opened read-only | OpenCode's models.dev data, then a preset | `install-hook opencode` (a plugin file) |
| Pi | Its session JSONL tree | Pi's model files or OpenCode's models.dev data; otherwise pass `--window` or set `CTXCHECK_WINDOW` | `install-hook pi` (an extension file) |
| Copilot CLI | Status-line sidecar | Sidecar | `install-statusline copilot` **and** `install-hook copilot` |
| Antigravity (`agy`) | Status-line sidecar | Sidecar | `install-statusline antigravity` **and** `install-hook antigravity` (warns before the next model call) |
| Cursor CLI | Status-line sidecar | Sidecar | `install-statusline cursor` **and** `install-hook cursor` |

Claude Code's measurement and backstop are verified live. The other backstops are built
from each runtime's documented hook contract and are not live-verified yet. The full
matrix, versions and caveats are in
[`skills/context-budget/references/runtimes.md`](skills/context-budget/references/runtimes.md).

Gates are derived from the measured window per role: 28% for workers and planners and 26%
for the coordinator on a window of 600K or more, 75% on 150K or more, 80% below that. A
plan's run-config can override them with `worker_handoff_pct` and `coordinator_handoff_pct`.

### Opt-in installers

Every runtime except Claude Code needs a one-time install for its backstop, and Copilot,
Antigravity and Cursor also need the status-line sidecar before they can be measured at
all. `<ctxcheck>` below is the installed `<skills dir>/context-budget/scripts/ctxcheck`.

```bash
python3 <ctxcheck> install-hook codex|cursor|copilot|antigravity|opencode|pi [--dry-run|--uninstall]
python3 <ctxcheck> install-statusline cursor|copilot|antigravity [--dry-run|--uninstall]
```

- `--dry-run` prints the diff and writes nothing. Run it first.
- `--uninstall` is the undo. It removes only what the install added and leaves every other
  entry (Orca's hooks, your own) byte-identical.
- JSON files are backed up to `<file>.ctxcheck-<UTC stamp>.bak` before each write, and a
  file that isn't strict JSON is refused. Re-running an install is a no-op.
- OpenCode and Pi get a whole file of their own, `ctxcheck-context-budget.ts`, in
  OpenCode's `plugins/` or Pi's `extensions/` directory. Uninstall deletes only that file.
- Codex asks you to trust the new hook in `/hooks`. Restart Copilot, OpenCode, Pi and
  `agy` sessions after installing.

### Status-line sidecars

Copilot CLI, Antigravity and Cursor CLI keep their context count in memory only, but each
passes it to a status-line command. `install-statusline` wraps your existing status-line
command as `python3 <ctxcheck> statusline-tee --runtime R -- '<original>'`. The tee saves
each payload as a snapshot under
`${CTXCHECK_HOME:-${XDG_STATE_HOME:-~/.local/state}/jelly-skills/ctxcheck}/statusline/`
and then runs your original command, so your status line looks the same. With no previous
command it prints `ctx NN%`.

Snapshots only refresh while the interactive TUI is on screen. ctxcheck refuses one older
than 15 minutes (exit 3), so headless runs of these three can't be measured.

### Coordinator succession

The successor coordinator is started with **the predecessor's own command line**:
`ctxcheck launch-command` walks the process tree to the agent CLI running this session and
prints its argv with resume arguments and the initial prompt stripped. So start the
coordinator with the posture you want the successor to have. For an unattended run of
Claude Code that means something like `claude --dangerously-skip-permissions`. Otherwise
the successor stops at a permission prompt; the predecessor tells you and doesn't re-send.

Succession needs an Orca terminal. Outside one, the coordinator writes the handoff note
and stops, and you start the successor yourself by running the same `/run-plan` line in a
fresh session.

### Copilot CLI is not an Orca worker

Orca has no Copilot agent id for dispatched workers, so a Copilot session can be the
run-plan coordinator or a planner you run by hand, never a phase worker.

### Testing

`python3 -m unittest discover -s tests -v` runs the offline suite. The live path (a
continuation leg and a coordinator succession inside a real Orca run) has a manual smoke
test in [`tests/e2e/README.md`](tests/e2e/README.md).

## Migrating from hand-installed copies

If you previously copied these skills by hand (as `session-program` and `run-plan` in
`~/.claude/skills/` or `~/.agents/skills/`), remove those copies before a global install.
Otherwise the old and new skills both load and both trigger.

## License

MIT
