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
  version: "0.1.0"
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

`ctxcheck` reads this session's own transcript and reports how many tokens
occupy the context window, out of how many. The agent CLIs show this only on
their status line, which the model never sees.

## Run it

Use the absolute path of this skill's directory, and always run it through
`python3`, because installs can drop the exec bit:

```bash
python3 <this skill's dir>/scripts/ctxcheck --json
```

The output includes `runtime`, `used_tokens`, `context_window`,
`window_source`, `used_percentage`, `remaining_tokens`, `model`, `session_id`,
`transcript`, `measure_source`, `stale_seconds` (the age of a status-line
snapshot) and, when a gate applies, `threshold_percentage` and
`over_threshold`.

Pass a role to get a handoff gate that is derived from the window:

```bash
python3 <this skill's dir>/scripts/ctxcheck --role worker   # or coordinator, planner
python3 <this skill's dir>/scripts/ctxcheck --gate 40       # an explicit gate always wins
python3 <this skill's dir>/scripts/ctxcheck --list-presets  # windows and the per-role bands
```

The bands aim to keep every session at roughly 150-280K tokens of carried
context: on a 1M window a worker or planner hands off at 28% and a coordinator
at 26%; on a 150K-600K window both hand off at 75%; on anything smaller, 80%.
With no `--role` or `--gate`, ctxcheck only reports usage.

## Exit codes

| Code | Meaning | What to do |
| --- | --- | --- |
| 0 | OK, or at or under the gate | Carry on. |
| 1 | Error (bad flag, unreadable transcript) | Fix the invocation; don't treat it as a budget signal. |
| 3 | This runtime can't be measured | You have no budget here. Say so once and carry on. |
| 10 | Gate exceeded | Stop at the next safe point and hand off as your instructions say. |

Check at boundaries, such as between phases or numbered edits, rather than
after every step.

## Arm, check, disarm

Arm once at the start of work that should measure itself. Arming records the
role, the gate and the window, so later checks need no flags:

```bash
python3 <this skill's dir>/scripts/ctxcheck --arm --role worker --label "session 3 of /abs/plan.md"
python3 <this skill's dir>/scripts/ctxcheck ; echo $?    # uses the marker: 0 ok, 10 over
python3 <this skill's dir>/scripts/ctxcheck --disarm      # on every way out
```

`--gate PCT` and `--window N` override the derived values. While a session is
armed, a post-tool hook also warns it once it goes over its gate:

- **On Claude Code**, invoking this skill registers the hook for the rest of
  the session.
- **On Codex**, the user runs `ctxcheck install-hook codex` once and trusts
  the hook in Codex's `/hooks`.

Which runtimes are measured, and how, is in
[references/runtimes.md](references/runtimes.md). Copilot CLI, Antigravity
and Cursor CLI need a one-time `ctxcheck install-statusline <runtime>`, which
the user runs; see "Status-line setup" there.
