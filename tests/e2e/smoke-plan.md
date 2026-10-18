# Smoke test: run-plan context budgets and coordinator succession

## Run config

```run-config
agent: claude
model: sonnet
branch: main
cwd: @SCRATCH@
worker_handoff_pct: 1
coordinator_handoff_pct: 1
```

## Context

This plan is a test fixture for the jelly-skills live smoke test (`tests/e2e/README.md` in
the jelly-skills repo). It exists only to make `run-plan` exercise its context budgets, so
its phases are deliberately trivial file edits in a throwaway repo.

Both gates are set to 1%, and every session is over 1% from its first turn. So:

- Every phase worker is over budget at its first check, which runs after its first
  numbered step lands. Phase 1 has two steps, so its first worker stops after step 1 and
  the phase continues in a second leg. Phases 2 and 3 have one step each, so their workers
  have only the commit left and finish.
- Coordinator generation 1 is over budget at its first boundary check, after phase 1's
  first leg is released. Phase 1's second leg and two more phases are still left, so it
  hands off to generation 2.
- Generation 2 resumes at that boundary without checking it (generation 1 already did)
  and dispatches phase 1's second leg. Its first check, after phase 1 completes, is over
  1% with phases 2 and 3 left, so it hands off to generation 3.
- Generation 3 likewise dispatches phase 2 unchecked. At its next check only the last
  phase is left, so it finishes the run instead of handing off again.

There are no tests in this repo. Report `Tests: none` in outcome records.

## Session prompts

### Session 1 prompt

```
Smoke-test phase 1 of 3. Work in @SCRATCH@ on branch main. Read the plan file first. This phase is a trivial fixture: do exactly these two numbered steps and nothing else.

1. Create `notes.txt` containing the single line `phase 1 step 1`.
2. Append the line `phase 1 step 2` to `notes.txt`, so it has exactly two lines.

There are no tests. Finish: git add -A && git commit -m "smoke: phase 1" (no push).
```

### Session 2 prompt

```
Smoke-test phase 2 of 3. Work in @SCRATCH@ on branch main. Read the plan file first. This phase is a trivial fixture: do exactly this one numbered step and nothing else.

1. Create `phase2.txt` containing the single line `phase 2 step 1`.

There are no tests. Finish: git add -A && git commit -m "smoke: phase 2" (no push).
```

### Session 3 prompt

```
Smoke-test phase 3 of 3. Work in @SCRATCH@ on branch main. Read the plan file first. This phase is a trivial fixture: do exactly this one numbered step and nothing else.

1. Create `phase3.txt` containing the single line `phase 3 step 1`.

There are no tests. Finish: git add -A && git commit -m "smoke: phase 3" (no push).
```
