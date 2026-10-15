---
name: session-plan
description: >-
  Plan work as a session program — phases each sized to fit one fresh session,
  a ready-to-paste kickoff prompt per phase, the whole thing landing on one
  branch as a single PR, and a hard stop after plan approval (hand off to a new
  session instead of implementing). Use this whenever the user asks to create a
  plan, plan something out, or structure a feature/refactor/migration — or
  mentions "session plan", "session program", "multi-session", "phases", "one
  PR", or "prompts for each session" — even when they don't name this skill.
  Small tasks are in scope too: they get a lean one-session fast-path plan
  instead of the full program ceremony.
license: MIT
compatibility: >-
  Any coding agent with a shell and git. Uses plan mode, structured questions,
  subagents and persistent memory when the agent has them. Pairs with the
  run-plan skill; install both.
metadata:
  author: pbean
  version: "1.0.0"
---

# Session Plan

Turn a piece of work into a phased, multi-session program: one plan, one
branch, one PR, and a ready-to-paste kickoff prompt for every phase.

## Why this shape

A planning session burns most of its context window on exploration. By the
time a good plan exists there isn't enough window left to implement it well,
and work that limps across compactions degrades. The fix: spend this session
producing a plan so complete that each phase can be executed by a *fresh*
session with a full window, needing nothing but its kickoff prompt plus the
durable anchors that prompt points at. The planning session never implements —
it plans, files the anchors, and hands off.

## Agent capabilities

This skill runs on any coding agent. Where it names a capability, use your
agent's version of it, or the fallback:

- **Plan mode** — use it if your agent has one (Claude Code: plan mode).
  Otherwise hold yourself to it: read-only exploration, no edits except the
  plan file, then present the plan and wait for explicit approval.
- **Asking the user** — use your agent's structured-question tool if it has one
  (Claude Code: AskUserQuestion); otherwise ask in chat and wait for the answer.
- **The plan file** — your agent's plan directory if it has one (Claude Code:
  `~/.claude/plans/`); otherwise `~/.agents/plans/<slug>.md`. Always cite it by
  its **absolute** path.
- **Subagents** — delegate research and exploration to them where available;
  otherwise do it inline.
- **Persistent memory** — a memory entry is one of the anchors below when your
  agent has auto-recalled memory; skip that anchor when it doesn't.

The companion **run-plan** skill executes the finished plan; it is installed
alongside this one, so its files are at `../run-plan/` relative to this
skill's directory.

## Workflow

### 1. Enter plan mode and explore

If not already in plan mode, enter it (or hold the read-only discipline from
"Agent capabilities" if your agent has none). Then explore until the design is
validated against the actual code, not sketched from memory:

- Read the relevant source and record exact anchors (`path/to/file.py:123`)
  for every integration point a phase will touch. Fresh sessions can't ask you
  follow-ups — the anchors are how they find their footing fast.
- Check related issues, PRs, and project memories for prior decisions, known
  traps, and conventions.
- Discover the repo's real verification loop: the exact test command, the
  lint/format gate, commit-message conventions, known pre-existing failures.
  These go into every phase prompt's finish sequence — get them right once,
  here.

Settle open design decisions with the user *during* planning (via your
agent's question tool, or in chat), never as TBDs in the plan — phase prompts must be executable
without judgment calls a fresh session can't make. One decision to always
settle here:

**The anchor.** Every program needs at least one durable, discoverable place a
fresh session can read its context from. The options combine:

- **GitHub issues** — best when the repo works issue-driven. One **primary
  tracking issue** carries the design and the phase checklist, and it is what
  the final PR closes. For multi-phase programs, also settle granularity: a
  single tracking issue, or one **sub-issue per phase** under the primary
  (`gh issue create --parent <primary>`), giving each session its own progress
  surface to comment on and close. Either way, every additional same-repo
  issue the plan files — a follow-up spotted during exploration, a spun-off
  nice-to-have — is linked under the primary too (`gh issue edit <primary>
  --add-sub-issue <n>`), so the whole program hangs from one issue and nothing
  floats loose;
- the **plan file** itself — at the location from "Agent capabilities";
  cite its full absolute path in the prompts;
- a **memory entry** — auto-recalled by future sessions in this project, if
  persistent memory is available.

Ask the user which anchors this program warrants. Recommend issue + plan file
+ memory when a GitHub remote exists; plan file + memory otherwise (drop
memory from either when your agent has none).

### 2. Cut phases sized for a single session

Each phase becomes one fresh session that must complete all of: load its
context, implement, write tests, run the full verification loop, commit, and
push — inside one context window. Size accordingly:

- One coherent mechanism or subsystem per phase. If a phase's concrete steps
  won't fit on roughly one screen, it's two phases.
- Every phase leaves the branch shippable: tests green, lint clean, committed,
  pushed. A phase that ends mid-refactor poisons the next session's start.
- Order by dependency; put docs, CHANGELOG finalization, the full-suite sweep,
  and PR opening in the final phase.
- Small jobs are allowed to be small: if exploration shows the whole thing
  fits one session, don't churn it through the full program ceremony — switch
  to the fast path below.

### 3. The small-task fast path

When the whole task — implement, test, verify, commit, open the PR — fits
comfortably in one fresh session, the program collapses to one phase, and the
plan should collapse with it:

- **Keep exploration proportionate.** Validate the handful of anchors the one
  prompt needs plus the repo's finish sequence; don't survey the subsystem
  the way a four-phase refactor would demand.
- **Write the compact plan variant** (see the fast-path section of
  `references/plan-template.md`): context and design merged into one short
  section, traps, a single session prompt, a two-line post-approval list. The
  prompt carries the whole design inline — at this size, the prompt IS the
  program.
- **Default to light anchors**: the plan file plus a memory entry (if your
  agent has memory). Offer a
  tracking issue but don't file one by default for a one-session task; if the
  user wants one, it's a single primary — no sub-issues.
- **Everything else still applies**: one branch, one PR ready-for-review, and
  the hard handoff — the fresh session does the work, not this one. The fast
  path trims the document, never the discipline.

### 4. One branch, one PR

The whole program lands as a single PR:

- Session 1 creates one branch off the default branch; every later phase
  checks that branch out and continues it.
- Each phase commits and pushes; the PR is opened in the **final** phase,
  ready for review (not draft).
- Split into multiple PRs only when genuinely forced — a later phase cannot
  proceed until an earlier part is *merged* (published-package or cross-repo
  dependency), not merely committed. If you split, the plan states why one PR
  was impossible.

### 5. Write the plan document

Read `references/plan-template.md` before writing the plan, and follow its
section structure. In brief: Run config (the agent/model/branch/cwd block
the run-plan skill executes), Context (problem, hard constraints, decisions the
user made), Final design (validated API surface with file:line anchors), Traps
carried into every phase, Phases, Verification, Session prompts, and
Post-approval steps. Make sure the *complete* document — session prompts
included — ends up in the plan file, since its path is one of the anchors.

### 6. Author the session prompts

This is the heart of the skill. Each phase gets one prompt in its own fenced
code block so the user can copy it whole. The reader is a fresh session with
zero conversation context — write for them:

- **Orientation first**: "We're implementing phase N of M of <anchor>" —
  and when phases have sub-issues, name both: "phase sub-issue #X under
  primary #Y". Then where to load context: view the tracking issue, read the
  plan file at its full **absolute** path (a dispatched session must be able
  to open it verbatim — never `~`-relative), check out the branch and read
  `git log -p origin/<default>..`, consult the program memory (if one was
  written). The diff read doubles as recovery — if a prior attempt at this
  phase died partway, the session sees the partial work and reconciles
  instead of duplicating it.
- **A hard scope fence**: "Scope for THIS session — <phase> only." Fresh
  sessions with a good plan in hand get ambitious; fence them in.
- **A standing working-style line**: "Use subagents (if your agent has them)
  for research, exploration, and mechanical parallelizable work; keep the main
  thread for edits and verification."
- **Concrete steps**, in implementation order, carrying the file:line anchors
  from exploration.
- **Tests to write**, named individually, each pointing at the existing test
  file and pattern to imitate.
- **Traps**, restated in every prompt — a fresh session hasn't read the other
  prompts. Known failing tests to ignore, tooling quirks, things not to touch
  and why.
- **The finish sequence**, spelled out with the repo's real commands: targeted
  tests → full suite → lint gate → commit (give the exact message) → push →
  update the anchors (progress comment on the issue — with per-phase
  sub-issues, close the phase's sub-issue; the primary stays open until the
  final PR closes it — and check the phase off in the program memory, if
  any) → update the plan file: flip this phase's `### Session N prompt`
  heading to `[done]` and append the outcome record (date, commit SHA,
  summary, deviations) after the prompt fence — exact shape in the run-plan
  skill's `../run-plan/references/plan-format.md` (relative to this skill's
  directory). Spell the shape out in the prompt itself; the phase session
  may not have the skills installed.

A prompt is done when you'd bet a fresh session could execute it without
asking a single question.

### 7. Present the plan, then hand off — never execute

Present the plan for approval as usual. **When the user approves, do not start
implementing.** Approval means the plan is right — not that this session
should spend its remaining context on phase 1. The default after plan approval
is to begin coding; this skill deliberately overrides that default, because a
fresh session with a full window will do phase 1 better than this one can.

After approval, do exactly the plan's Post-approval steps and nothing more:

1. Create the anchors the user agreed to: file the primary tracking issue;
   when per-phase sub-issues were agreed, file each phase's issue under it
   with `gh issue create --parent <primary>`; then substitute the real
   numbers into every session prompt in the plan file.
2. If your agent has persistent memory, write or update the program memory:
   issue and branch, plan-file path, a phase checklist (✅/⬜) that future
   sessions keep current, and the traps.
3. In the final message: tell the user to open a fresh session in the
   program's working directory and invoke the run-plan skill on the
   absolute plan path (Claude Code: `/run-plan <absolute plan path>`; other
   agents: "run the plan at <absolute plan path>") — it dispatches every
   phase as its own fresh session and updates the plan as it goes. Print the Session 1 prompt in full only if they say they'd rather
   drive a phase manually by pasting it. Then stop.

No branch creation, no code edits, no "quick head start" — Session 1's prompt
covers all of that, and it deserves a full window.
