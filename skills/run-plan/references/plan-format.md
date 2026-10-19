# The plan-format contract

This file is the **canonical** contract between the `session-plan` skill (which writes
plans) and the `run-plan` skill (which executes them). Both skills cite it; when it changes,
check both sides in the same edit. Plans remain human-readable and hand-editable throughout —
every machine-parsed element below doubles as something a person scanning the file would
write anyway.

## 1. Run config

Immediately after the `# Title` line, the first ` ```run-config ` fence in the file:

````markdown
## Run config

```run-config
agent: claude
model: opus
branch: <branch-name>
cwd: /abs/path/to/repo
```
````

- Required keys: `agent`, `branch`, `cwd`. `agent` is the backend's agent id: an Orca agent
  id (`claude`, `codex`, `cursor`, `opencode`, … — `worker-start --help` lists the current
  set), or a herdr `--kind` (`herdr agent` lists the kinds); `cwd` is where phase sessions
  run; `branch` is the program's shared branch.
- Optional keys: `backend` (`orca` | `herdr`: the dispatch backend; detected when absent,
  see the last bullet), `agent_args` (herdr only: a string passed verbatim after `--` to
  `herdr agent start`, typically permission flags such as `--dangerously-skip-permissions`
  for an unattended `claude`), `model` (a provider model id, passed to the backend only
  when present — omit it for agents that take their model from their own config; `worker-start --help`
  lists which agents accept one), `effort` (a reasoning-effort level such as `high`, passed
  only together with `model` and only for agents that support it; leave it out to keep the
  agent's default), `max_consecutive_failures` (default 3), `continuation_cap` (default 3), and two context
  gates, each a percentage of the agent's context window:
  - `worker_handoff_pct`: the gate each phase worker arms with (§7).
  - `coordinator_handoff_pct`: the gate the runner arms itself with.

  When a gate key is absent, the gate is derived from the measured window per the
  context-budget skill's role gates (`ctxcheck --role worker` or `--role coordinator`).
  Set one only to override that band.
- The runner parses only the **first** `run-config` fence. Unknown keys are ignored, not
  errors.
- If the block or a required key is missing, the runner synthesizes defaults (`agent` = the
  coordinator's own agent when the backend supports it as a worker (an Orca agent id, or a
  herdr kind), otherwise ask; `model: opus` only
  when `agent: claude`, otherwise none; `cwd` = the current worktree; `branch` recovered
  from plan prose), confirms the synthesized block with the user **once** at run start, and
  writes it into the plan during normalization. `backend` is resolved before any other
  default is synthesized, since the `agent` default depends on it.
- When `backend` is absent, the runner detects it: a non-empty `$ORCA_TERMINAL_HANDLE`
  means `orca`, and `HERDR_ENV=1` means `herdr`. Both or neither means ask the user once,
  with `orca` as the default answer: a plan with no `backend` key and nothing detectable
  keeps today's default, `orca`. The runner resolves it first (run-plan Step 1, before
  normalization), writes it into the run-config during normalization, and records it in
  `run-state.json`. On resume and in successors, `run-state.json`'s `backend` wins over
  the plan and over detection, so a run never changes backend mid-flight. A
  `run-state.json` without a `backend` key comes from an Orca-only runner (1.3 and
  older) and means `orca`.

## 2. Phase unit and status tags

The runner's unit of work is the `### Session N prompt` heading inside the plan's
`## Session prompts` section, numbered 1..M with no gaps. Status is an ASCII tag suffix on
the heading line:

```
### Session 3 prompt                 ← pending (absence of a tag = pending)
### Session 3 prompt [running]
### Session 3 prompt [done]
### Session 3 prompt [blocked]
```

Parse rule: `^### Session (\d+) prompt(?:\s*\[(running|done|blocked)\])?\s*$`

Absence-is-pending is deliberate: a freshly approved session-plan plan is runnable with
zero annotation, and the tags stay legible to a human scanning the file. (Plans normalized
from legacy layouts keep their original heading text; the tag is appended to whatever the
heading line is — see §6.)

## 3. Prompt extraction

Phase N's prompt is the **first fenced code block after its heading**, with one legacy
accommodation: if a literal line `**Prompt for the session:**` appears under the heading,
the prompt is the first fence after *that* line instead (this skips legacy outcome
blockquotes, which may themselves contain fences or tables).

Extraction happens **once, at normalization**, into `<runs dir>/phase-N.src.txt` (the
runs directory is defined in SKILL.md Step 0). After that the runner parses only headings,
tags, and run-config — never fence contents — except that it re-extracts a phase's prompt
at dispatch time so boundary-time edits by the user are honored.

## 4. Outcome record

Appended by the phase's worker **after the closing fence** of its prompt, under the same
heading, before the next `###`:

```markdown
> **Outcome (2026-08-21):** <1–3 line summary of what landed>
> Commit: <sha> | Branch: <branch> | Tests: <state>
> Deviations: <what was done differently from the plan, or "none">
```

Optionally followed by lines addressed to later phases:

```markdown
> **Note for Session M:** <what that session must know>
```

Later sessions see these because every dispatched prompt orders reading the plan first.
Outcome records are for humans and resuming coordinators; the runner never structurally
parses them (it reads SHAs from them for the end-of-run summary, best-effort).

## 5. Write ownership

Phases run strictly sequentially, so ownership windowing is the entire concurrency story:

- A **worker** writes exactly two things in the plan: flip its **own** heading tag to
  `[done]`, and append its outcome blockquote under its own heading. It never edits any
  fenced prompt block (including its own), any other phase's section, the run-config block,
  or another phase's tag. It re-reads the plan before editing and edits surgically.
- The **runner** writes only between phases: it sets `[running]` before dispatch and does
  not touch the plan again until that worker is gone. On failure paths it sets `[blocked]`
  and appends a `> **Blocked (date):** <cause, dispatch id, leg>` blockquote.
- **Backfill:** if a worker reported `PHASE-STATUS: complete` but forgot the plan edit, the
  runner flips the tag and writes a minimal outcome record from the `worker_done` body,
  marked `(recorded by runner)`, and logs a warning in `run-log.md`. A completed phase is
  never failed over missing bookkeeping — but recurring backfill is surfaced in the
  end-of-run summary because it means the wrapper suffix isn't landing.

## 6. Normalization of legacy plans

Run once, at run start, before any dispatch.

**Detect layout:**

- **L1** — a `## Session prompts` section with `### Session N prompt` headings: already the
  contract; no confirmation needed unless a status tag is ambiguous.
- **L2** — `### Phase N — <title> [— <emoji> **STATUS (date)**]` headings, optional outcome
  blockquotes, a `**Prompt for the session:**` line, then the fence.
- **L3** — `### Phase N — <title>` with the prompt fence immediately after the heading.
- **None** — no heading anywhere followed by a prompt fence → **refuse**: "This plan has no
  per-phase session prompts. Produce one with the session-plan skill, or add
  `### Session N prompt` sections each containing one fenced prompt." Never invent phases
  out of prose.

**Status translation:** `✅` / `**COMPLETE**` / `DONE` → `[done]`. `🚧` / `OPEN` / partial
wording → ask the user (done vs pending). No marker → pending.

**Confirm the mapping** (L2/L3, or any ambiguous status): show the user a table — phase
number, title, translated status, first line of the extracted prompt — and get a yes before
touching the file. This and the run-config confirmation are the only interactive moments in
a run.

**Annotate in place, never restructure:** append the ASCII status tag to each existing
phase heading (whatever its form), and insert the `## Run config` block after the title.
Do not move prose, outcome blockquotes, or fences. Record each phase's literal heading line
in `run-state.json`; after normalization the runner re-finds headings by those recorded
literals, so the legacy heading form only ever matters for tag maintenance.

**Extract** every pending phase's prompt to `phase-N.src.txt` in the same pass, and log a
normalization summary in `run-log.md`.

## 7. Wrapper suffix — canonical text

The runner assembles each dispatched phase file as: backend first-contact preamble →
orientation prefix → the raw prompt verbatim → this suffix → backend completion paragraph.
**The wrapper lives only in the phase file, never in the plan's fences** — hand-pasted
prompts must stay free of orchestration noise. The backend paragraphs come from the
dispatch reference; the backend-free paragraphs here are canonical.

**Substitutions.** The runner fills the placeholders once per phase file:

- `<abs plan path>`, `<literal heading line>`, `N` (phase number), `<runs dir>` and `<g>`
  (this leg's number: 1 for the first worker, then 2, 3, … for continuations).
- `<ABS_CTXCHECK>`: the absolute path of `ctxcheck`. The runner resolves it **once, at run
  start**, from the sibling skill, `../context-budget/scripts/ctxcheck` relative to the
  run-plan skill directory. It records the path in `run-state.json` as `ctxcheck_path` and
  reuses it for every phase file.
- `[--gate <worker_handoff_pct>]`: becomes `--gate <value>` when run-config sets
  `worker_handoff_pct`. Otherwise it is deleted, and ctxcheck derives the gate.

If the context-budget skill isn't installed beside run-plan, the run goes ahead without
budgets. The runner then leaves out the **Context budget** and **Disarm** paragraphs and
the over-budget paragraph's first sentence (the pointer to `references/worker.md`), and
keeps the rest.

> **Working style:** use subagents for research, exploration, and mechanical parallelizable
> work; keep the main thread for edits and verification. (If the prompt above already says
> this, it applies once.)
>
> **When the phase is done, update the plan file at `<abs plan path>`:** flip this phase's
> heading — `<literal heading line>` — to end in `[done]`, and append the outcome record
> after the prompt's closing fence:
>
> ```
> > **Outcome (<date>):** <1–3 line summary>
> > Commit: <sha> | Branch: <branch> | Tests: <state>
> > Deviations: <or "none">
> ```
>
> Add `> **Note for Session M:** ...` lines for anything a later phase must know. Touch
> nothing else in the plan: not the fenced prompts (including this phase's), not other
> phases' sections or tags, not the run-config block.
>
> **Context budget:** this session measures its own context. On Claude Code, first invoke
> the `context-budget` skill, which registers its backstop hook. On any other runtime, run
> the commands below directly. Before your first edit, arm:
> `python3 <ABS_CTXCHECK> --arm --role worker --label "session N of <abs plan path>" [--gate <worker_handoff_pct>]`.
> After each numbered step lands, and once more before starting any ablation check, run
> `python3 <ABS_CTXCHECK>` and read its exit code. 0 means carry on. 3 means this runtime
> can't be measured: say "no context budget on this runtime" once and carry on. 10, or a
> backstop warning, means you are over budget: don't start the next numbered step; follow
> the paragraph below.
>
> **If you are over budget, or cannot finish within your context window:** follow the
> context-budget skill's `references/worker.md` (two directories up from `<ABS_CTXCHECK>`)
> for the full procedure. Stop at a safe point between steps — never mid-edit, never with a
> test-ablation still reverted. Leave your edits uncommitted in the worktree, and save a
> patch backup to `<runs dir>/phase-N-wip-leg<g>.patch`. Write (or append to, under a
> `## Leg <g>` heading, if you are a later leg) `<runs dir>/phase-N-continuation.md` for a
> successor that has this same prompt and no memory of your session: which steps are
> landed and which remain, `git status --porcelain` output, test pass/fail state, anything
> you learned that **contradicts this prompt**, and explicitly what you have **not**
> verified — everything you learned and did not write down is lost. Then report completion
> as the dispatch instructions below describe, with `PHASE-STATUS: continuation-required`
> as the first line of the body.
>
> **Disarm on both exits:** run `python3 <ABS_CTXCHECK> --disarm` after the commit and
> plan update when the phase completes, or just before reporting when you stop for a
> continuation.
