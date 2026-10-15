---
name: context-budget
description: >-
  Measure how full this agent session's context window is, against a
  per-role handoff gate, with the bundled ctxcheck script. Use when the user
  asks about the "context budget", says "check my context", "ctxcheck", "how
  full is my context", or "arm the context backstop", or when a plan or phase
  prompt tells you to check your context at a boundary.
license: MIT
compatibility: >-
  Python 3.9+ (stdlib only). Measures Claude Code, Codex CLI, OpenCode (v1
  and v2) and Pi sessions from their own session data, and Copilot CLI,
  Antigravity and Cursor CLI through an opt-in status-line sidecar; on any
  other runtime ctxcheck reports that the session cannot be measured (exit 3).
metadata:
  author: pbean
  version: "0.2.0"
hooks:
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          timeout: 10
          command: >-
            d="${CTXCHECK_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/jelly-skills/ctxcheck}/markers";
            if ls "$d"/*.json >/dev/null 2>&1; then
            for s in "$CLAUDE_PLUGIN_ROOT" "$CLAUDE_PROJECT_DIR/.claude/skills/context-budget"
            "$HOME/.claude/skills/context-budget" "$HOME/.agents/skills/context-budget"; do
            if [ -f "$s/scripts/ctxcheck" ]; then
            exec python3 "$s/scripts/ctxcheck" --hook --runtime claude; fi; done; fi;
            cat >/dev/null; exit 0
---

# context-budget

A session that fills its context window doesn't crash. It degrades quietly:
it re-reads files it already read, loses track of which edits landed, and
reports work it can no longer vouch for. From the outside that report looks
exactly like a good one. The agent CLIs know how full the window is, but they
show it only on their status line, which the model never sees.

`ctxcheck` reads this session's own session data and reports how many tokens
occupy the context window, out of how many, against a per-role handoff gate.
This skill also holds the doctrine for what to do when a session goes over:

- [references/worker.md](references/worker.md): a phase worker hands the rest
  of its phase to a fresh worker.
- [references/planner.md](references/planner.md): a planner saves its draft
  and a continuation note, then stops.
- The coordinator's succession lives in the run-plan skill
  (`references/coordinator-handoff.md` there).

## Roles, and who arms which

| Role | Who | Who arms it |
| --- | --- | --- |
| `worker` | A phase session dispatched by run-plan | The worker itself, when its phase file says to |
| `planner` | A session-plan planning session | The planner itself, as the first action of planning |
| `coordinator` | The run-plan coordinator | The coordinator itself, at the start of the run (see run-plan) |

Hand-pasted phase sessions don't measure themselves unless their prompt asks
them to. Nothing stops you arming any session, though.

## Find the script

The script is `<this skill's dir>/scripts/ctxcheck`. Use the absolute path of
this skill's directory, and always run it through `python3`, because installs
can drop the exec bit:

```bash
python3 <this skill's dir>/scripts/ctxcheck --json
```

Below, `ctxcheck` is short for `python3 <this skill's dir>/scripts/ctxcheck`.

## Arm at the start

Arm once, before the first real work. Arming records the session's role, its
gate and its measured window in a marker, so later checks need no flags:

```bash
ctxcheck --arm --role worker --label "session 3 of /abs/plan.md"
ctxcheck --arm --role planner --label "planning <topic>"
ctxcheck --arm --role coordinator --label "run-plan /abs/plan.md" --gate 30
```

- `--label` is free text naming the work. The backstop's warning quotes it.
- `--gate PCT` overrides the derived gate. Pass it only when your
  instructions give one (for example a run-config handoff percentage).
- `--window N` overrides the discovered window. Pass it only when ctxcheck
  can't discover one (it says so, with exit 3).
- Arming also reaps markers that a killed session left behind more than 7
  days ago.
- The marker lives in ctxcheck's own state dir
  (`${CTXCHECK_HOME:-${XDG_STATE_HOME:-~/.local/state}/jelly-skills/ctxcheck}`),
  never in the project. Arming is not a project edit, so it is allowed in
  plan mode and other read-only phases.

**On Claude Code, invoke this skill first.** Invoking it is what registers its
backstop hook (the `hooks:` block in this file's frontmatter) for the rest of
the session. Running the script without invoking the skill measures fine but
arms no backstop.

## Check at boundaries only

After arming, a plain `ctxcheck` uses the marker's role, gate and window:

```bash
ctxcheck ; echo $?        # 0: carry on. 10: over the gate.
```

Check at these boundaries, not mid-edit and not on a timer:

- **worker:** after each numbered step of the phase lands, and once more
  before starting any ablation check (reverting source to prove a test
  catches it).
- **planner:** after exploration, after the design settles, and before
  writing the session prompts.
- **coordinator:** at phase boundaries, with no worker in flight. The details
  are in the run-plan skill.

Each check costs a little context, so don't poll.

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | OK, or at or under the gate | Carry on. |
| 1 | Error (bad flag, unreadable transcript) | Fix the invocation. It is not a budget signal. |
| 3 | This runtime can't be measured | You have no budget here. Say "no context budget on this runtime" once, then carry on without checking again. |
| 10 | Gate exceeded | Stop at the next safe point and follow your role's procedure: [worker](references/worker.md), [planner](references/planner.md), or the run-plan coordinator handoff. |

Exit 3 is not an error. Some runtimes can't be measured at all, and a status-line
runtime whose snapshot is older than 15 minutes is treated the same way.

## Disarm on every exit

```bash
ctxcheck --disarm
```

Disarm when the session's work ends, by any route: the phase committed, the
plan handed off, a handoff to a successor, or a stop because something is
blocked. A stale marker keeps the backstop warning a session that is no
longer doing the armed work.

## The backstop

While a session is armed, a post-tool hook runs `ctxcheck --hook`. It stays
silent until the session is over its gate. Then it adds one role-specific
instruction to the model's context, and it warns again only after usage
climbs another 5 points. It never blocks a tool call. Treat its warning as
exit 10 from a check.

- **Claude Code:** automatic once this skill is invoked (live-verified).
- **Codex CLI:** the user runs `ctxcheck install-hook codex` once and trusts
  the hook in Codex's `/hooks`.
- **OpenCode and Pi:** the user runs `ctxcheck install-hook opencode` or
  `ctxcheck install-hook pi` once. It installs a small plugin or extension
  file that appends the warning to the tool result.
- **Cursor CLI, Copilot CLI and Antigravity:** these are measured from a
  status-line sidecar, so the user runs **both**
  `ctxcheck install-statusline <runtime>` and `ctxcheck install-hook <runtime>`
  once. Antigravity's warning arrives before the next model call
  (`PreInvocation`), not after a tool call.

Only Claude Code's backstop is live-verified. The others were built from the
vendors' documented contracts and tested against synthetic data. Every
installer takes `--dry-run` and `--uninstall`. Leave them to the user: they
edit the user's agent configuration. Setup, files touched, undo steps and
known gaps per runtime are in [references/runtimes.md](references/runtimes.md).

## Gates

With `--role` (or a marker), the gate is derived from the measured window:

| Window | worker / planner | coordinator |
| --- | --- | --- |
| 600K or more | 28% (~280K on 1M) | 26% (~260K on 1M) |
| 150K to 600K | 75% | 75% |
| smaller | 80% | 80% |

What rots a session is the *absolute* context it carries, not the fraction of
its window. A single percentage can't serve every runtime: 28% of a 1M window
is a whole phase's work, while 28% of Codex's ~258K window is 72K, which would
stop a worker before it finished orienting. So the bands land every session at
roughly 150-280K tokens. `--gate` always wins over the band.
`ctxcheck --list-presets` prints the known windows and bands.

With no `--role`, no `--gate` and no marker, ctxcheck only reports usage.

## Reading the report

`ctxcheck --json` prints `runtime`, `role`, `used_tokens`, `context_window`,
`window_source`, `used_percentage`, `remaining_tokens`, `model`,
`session_id`, `transcript`, `measure_source` (`transcript` or `statusline`),
`stale_seconds` (the age of a status-line snapshot), and the threshold keys
`threshold_percentage`, `threshold_source` and `over_threshold` (null when no
gate applies). Other flags: `--runtime R` skips detection, `--session ID` and
`--transcript PATH` point at a specific session.
