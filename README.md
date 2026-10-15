# jelly-skills

Two companion [Agent Skills](https://agentskills.io) for running large changes as a series
of fresh agent sessions, installable with the [`skills` CLI](https://github.com/vercel-labs/skills).

| Skill | What it does |
| --- | --- |
| **session-plan** | Plans a feature, refactor or migration as a *session program*: phases each sized to fit one fresh session, a ready-to-paste kickoff prompt per phase, everything landing on one branch as one PR. It stops after the plan is approved and hands off instead of implementing. Small tasks get a one-session fast path. |
| **run-plan** | Runs a session-plan plan unattended: dispatches each pending phase to a fresh agent session through Orca orchestration, waits for it, checks the plan file was updated, and moves on. It stops when the plan is done or a phase is blocked. |

The flow is: ask your agent to plan something (session-plan triggers), approve the plan,
then open a fresh session and run the plan (`/run-plan <abs plan path>` in Claude Code, or
"run the plan at `<path>`" elsewhere). Each phase also works by hand: paste its prompt into
a new session.

## Install

Install **both skills together**. session-plan reads run-plan's
`references/plan-format.md` through the sibling path `../run-plan/`.

```bash
# from GitHub, once published
npx skills add pbean/jelly-skills --skill '*'

# from a local checkout
npx skills add ./jelly-skills --skill '*'

# list without installing
npx skills add pbean/jelly-skills --list

# user-level (all projects), for specific agents
npx skills add pbean/jelly-skills --skill '*' -g -a claude-code -a codex
```

Without `-g`, skills install into the current project (`.claude/skills/` for Claude Code,
`.agents/skills/` for Codex, Cursor, OpenCode and others).

### Prerequisites

- `git`; `gh` is optional and only needed when a plan uses GitHub issues as anchors.
- run-plan needs the **Orca CLI** with orchestration. On Linux, call it as `orca-ide`.
  Never call bare `orca` there: that runs the GNOME screen reader.

## Agent support

Both skills are agent-neutral. They use these features when the agent has them, and fall
back otherwise:

| Capability | Used for | Fallback |
| --- | --- | --- |
| Plan mode (Claude Code) | Read-only planning, approval gate | Self-imposed: no edits but the plan file; wait for explicit approval |
| Structured questions (e.g. AskUserQuestion) | Settling design decisions | Ask in chat |
| Plan directory (`~/.claude/plans/`) | Where the plan file lives | `~/.agents/plans/<slug>.md` |
| Subagents | Research / exploration in phase sessions | Inline |
| Persistent memory | A program-memory anchor | Skipped; plan file (+ issue) anchors only |
| Slash commands | `/run-plan <path>` | Ask the agent to "run the plan at `<path>`" |

The plan's `run-config` block chooses which agent the run-plan **workers** are, using any
Orca agent id (`claude`, `codex`, `cursor`, `opencode`, …). `model` is optional; leave it
out for agents that read the model from their own config.

Run state goes to `<plan dir>/runs/<plan-name>/`. If the plan file is inside a git repo,
it goes to `~/.agents/plans/runs/<plan-name>/` instead.

## Migrating from hand-installed copies

If you previously copied these skills by hand (as `session-program` and `run-plan` in
`~/.claude/skills/` or `~/.agents/skills/`), remove those copies before a global install.
Otherwise the old and new skills both load and both trigger.

## License

MIT
