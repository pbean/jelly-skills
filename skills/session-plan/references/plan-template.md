# Plan document template

The plan document is the file the planning session persists (plan mode's file,
where the agent has one); its full path becomes one of the program's anchors,
so keep the structure predictable. Sections in order:

## `# <Title>` — what the program delivers, plus the issue ref once one exists

### `## Run config`

Right after the title, the block the run-plan skill executes when the user
runs the plan instead of pasting prompts by hand:

````markdown
```run-config
agent: claude
model: opus
branch: <branch-name>
cwd: /abs/path/to/repo
```
````

`agent`, `branch`, and `cwd` are required; `cwd` is where phase sessions run.
`agent` is an Orca agent id (`claude`, `codex`, `cursor`, `opencode`, …) —
default to the agent doing the planning. `model` is optional: set it to a
provider model id for agents Orca can pick a model for (e.g. `opus` for
`claude`), and omit it for agents that take the model from their own config
(e.g. `opencode`). Parse rules and optional keys live in the run-plan skill's
`plan-format.md` — `../../run-plan/references/plan-format.md` relative to this
file. That file is the canonical contract between this template and the
runner; keep the two in step.

### `## Context`

The problem statement and how we got here (link prior PRs/issues). Then the
phase list as a numbered overview, one or two lines each, so a reader gets the
whole arc before the detail. State **HARD CONSTRAINTS** in bold — invariants
no phase may violate — with the evidence for why they're load-bearing. Record
decisions the user made during planning, with the date.

### `## Final design`

The validated design: new and changed API surface, organized per file, with a
file:line anchor for every integration point. Write it so a fresh session can
*verify* each claim quickly rather than re-derive it. Include the reasoning
that isn't visible in the code (why field X goes last, why the hook lives on
the base class) — this is where design arguments survive the session that made
them.

### `## Traps carried into every phase`

Repo-level gotchas every session inherits: known pre-existing test failures to
ignore, tooling quirks with the exact command that avoids them, test-suite
conventions, files or functions not to touch and why, PR conventions (e.g.
ready-for-review, never draft).

### `## Phases (one fresh session each, shared branch <branch-name>)`

One bullet per phase: scope in a sentence, and what "done" means for it. Note
that every session ends with the finish sequence and the branch stays
shippable at each boundary. The final phase additionally carries: docs,
CHANGELOG finalization, the full sweep, and opening the PR ready for review.

### `## Verification (end-to-end, final phase)`

The acceptance checklist for the whole program: the named proof-point tests,
expected suite state, doc/config sync checks.

### `## Session prompts`

One `### Session N prompt` per phase, each containing a single fenced code
block (see anatomy below). If the tracking issue doesn't exist yet, use a
placeholder number and note it gets substituted post-approval.

These headings are also the runner's status surface: a bare heading means the
phase is pending; the run-plan runner and the phase sessions append `[running]`,
`[done]`, or `[blocked]` to the heading line as the run progresses, and each
completed phase gets a blockquote outcome record (date, commit SHA, summary,
deviations, optional `> **Note for Session M:**` lines) appended after its
prompt's closing fence. Nothing but the tag and the outcome record is ever
added to a phase's section, and the fenced prompts themselves are never
edited after approval.

### `## Post-approval steps for THIS planning session`

The bookkeeping-only list: create the primary tracking issue (if agreed) with
the design and phase checklist in its body; when per-phase sub-issues were
agreed, create each phase's issue under the primary —

```
gh issue create --title "<program>: S<N> — <phase scope>" --body "..." --parent <primary>
```

— and if planning spun off any other same-repo issues (follow-ups,
nice-to-haves), attach them with `gh issue edit <primary> --add-sub-issue <n>`
so everything hangs from the primary. Then substitute the real numbers into
every prompt; write the program memory if the agent has one (phase checklist
✅/⬜, branch, plan path, traps); tell the user to invoke the run-plan skill
on `<absolute plan path>` in a fresh session (Claude Code: `/run-plan
<absolute plan path>`) — print the Session 1 prompt only if they'd rather
drive it manually by pasting. State explicitly: no implementation happens in this
session.

## Session prompt anatomy

Every prompt follows this shape — orientation, scope fence, steps, tests,
traps, finish sequence:

```
We're implementing phase 2 of 4 of <program name> (sub-issue #X under primary
issue #N). Read both first: `gh issue view N` carries the full design,
`gh issue view X` this phase's scope. Also read the plan file
<absolute plan path> and the memory <program-memory> if present. Phase 1
is already on branch <branch> — check it out and read the diff
(`git log -p origin/main..`) before starting.

Scope for THIS session — <mechanism> only.

Use subagents (if your agent has them) for research, exploration, and
mechanical parallelizable work; keep the main thread for edits and
verification.

1. src/pkg/module.py (~line 123): <concrete change, with the shape of the new
   code and where it slots in>
2. src/pkg/other.py (~line 456): <next step>
3. CHANGELOG.md: extend the program's consolidated entry with the phase-2
   line.

Tests (follow existing patterns exactly):
- tests/test_module.py (pattern at ~line 789): test_<name> — <what it proves>
- tests/test_other.py: test_<name> — <what it proves>

TRAPS: <known failing tests to ignore>; <tooling quirk + the exact safe
command>; <thing not to touch and why>.

Finish: <targeted test command> → <full suite command> → <lint command> →
commit ("<exact conventional-commit message>") → push → close sub-issue #X
with a progress comment (the primary #N stays open — the final PR closes it)
and check off phase 2 in memory <program-memory> (if present) → update the
plan file at <absolute plan path>: flip the `### Session 2 prompt` heading to
`[done]` and append this outcome record after this prompt's closing fence:
  > **Outcome (<date>):** <1–3 line summary>
  > Commit: <sha> | Branch: <branch> | Tests: <state>
  > Deviations: <or "none">
plus `> **Note for Session M:** ...` lines for anything a later phase must
know; touch nothing else in the plan.
```

(With a single tracking issue and no sub-issues, the orientation line names
only issue #N and the finish sequence comments progress on #N instead of
closing anything.)

The self-containment test: a session that has read *nothing but this prompt*
(plus the anchors it names) can execute the phase without asking a question.

## Small-task fast path template

When the whole job fits one session, collapse the document — merged sections,
one prompt, light anchors:

- `# <Title>` — one line on what lands.
- `## Run config` — same block as above; a one-phase plan is trivially
  runnable and gets it too.
- `## Context & design` — the problem, the validated design with its
  file:line anchors, and dated decisions, in a page or less. Include the
  sizing call explicitly ("fits one session because ...") so the reader knows
  the fast path was a decision, not an omission.
- `## Traps` — the repo gotchas this one session inherits.
- `## Session prompts` — ONE `### Session 1 prompt` heading (this exact
  shape, so the run-plan skill can execute the plan) with a single fenced
  block carrying the whole design inline: orientation (absolute plan-file
  path, memory name if any, fresh branch off the default branch), scope fence including an
  explicit out-of-scope list, the subagents working-style line, anchored
  steps, named tests, traps, and a finish sequence that ends by opening the
  PR ready-for-review and updating the plan (heading → `[done]`, outcome
  record after the fence).
- `## Post-approval steps` — two lines: write the memory entry (if the agent
  has memory), tell the user to invoke the run-plan skill on `<absolute plan
  path>` in a fresh session (Claude Code: `/run-plan <path>`) (or print
  the prompt if they'd rather paste it). No tracking issue by default (offer
  it; a single primary if taken), and no implementation here.
