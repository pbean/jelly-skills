# jelly-skills

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Agent Skills](https://img.shields.io/badge/format-Agent%20Skills-8a2be2.svg)](https://agentskills.io)

The [Agent Skills](https://agentskills.io) I've written and use every day, collected in one
place. They work with Claude Code, Codex, OpenCode, Pi, Cursor, Copilot CLI and any other agent
that loads `SKILL.md` skills, and they install with the
[`skills` CLI](https://github.com/vercel-labs/skills).

The collection grows as I write new skills and settle on them for daily use.

> **Why "jelly"?** My last name is Bean, and my kid's nickname is Jellybean.

## Skills

### Session programs

Run a large change as a series of fresh agent sessions instead of one long session that fills
its context window and degrades. These three are designed to work together.
**[Full guide →](docs/session-programs.md)**

| Skill | Version | What it does |
| --- | --- | --- |
| [**session-plan**](skills/session-plan/SKILL.md) | 1.1.2 | Plans a feature, refactor or migration as phases that each fit one fresh session, with a ready-to-paste prompt per phase and everything landing as one PR. Stops after you approve the plan. |
| [**run-plan**](skills/run-plan/SKILL.md) | 1.4.0 | Runs a session-plan plan unattended. Each phase goes to a fresh agent session through [Orca](https://github.com/stablyai/orca) or [herdr](https://herdr.dev), and the run stops when the plan is done or a phase is blocked. |
| [**context-budget**](skills/context-budget/SKILL.md) | 1.1.1 | Measures how full a session's context window is with the bundled `ctxcheck` script, and tells a planner, worker or coordinator when to hand off. Works on its own too. |

A typical run: ask your agent to plan something, approve the plan, then open a fresh session
and say `/run-plan <abs plan path>` (or "run the plan at `<path>`"). You can also run any phase
by hand by pasting its prompt into a new session.

## Install

You need Node.js for `npx`. Each skill may have its own prerequisites, listed in its guide.

```bash
# see what's available
npx skills add pbean/jelly-skills --list

# install everything into the current project
npx skills add pbean/jelly-skills --skill '*'

# install for your user (all projects), for specific agents, without prompts
npx skills add pbean/jelly-skills --skill '*' -g -a claude-code -a codex -y

# install selected skills
npx skills add pbean/jelly-skills --skill context-budget

# update installed skills later
npx skills update
```

Without `-g`, skills install into the current project (`.claude/skills/` for Claude Code,
`.agents/skills/` for Codex, Cursor, OpenCode and others). Repeat `-a` once per agent; the
CLI prints the full list of agent ids if you pass one it doesn't know. Agent sessions that are
already running keep the skills they loaded, so start a new session after you install or update.

**Skills that depend on each other must be installed together.** session-plan needs run-plan
and context-budget, and run-plan needs context-budget. They find each other as sibling
directories, so all of them have to be in the same skills directory.

To try a local checkout, pass its path in place of `pbean/jelly-skills`.

## Repository layout

```text
skills/<name>/
  SKILL.md        # frontmatter (name, description, version) + instructions
  references/     # docs the skill loads on demand
  scripts/        # bundled tooling, if any
docs/             # longer guides, one per skill group
tests/            # offline test suite; tests/e2e/ has manual smoke tests
CHANGELOG.md      # one entry per skill release
```

Each skill has its own version in its `SKILL.md` metadata, and its changes are logged
in [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
python3 -m unittest discover -s tests -v
```

The suite is offline and uses only the standard library (Python 3.9+). Besides testing code, it
checks that every command, flag and file the skills' Markdown mentions really exists, so the
docs can't drift from the tooling. Anything that needs a live agent, like a real run-plan
dispatch, is covered by the manual smoke tests in [`tests/e2e/`](tests/e2e/README.md).

## Contributing

Issues and pull requests are welcome. These skills grew out of my own workflow, so I may keep
some opinionated choices. Bug reports, support for other agents and runtimes, and doc fixes
are the easiest to accept. If you change a skill, update its version in `SKILL.md`, add a
CHANGELOG entry and keep the test suite passing.

## License

[MIT](LICENSE) © 2026 pbean
