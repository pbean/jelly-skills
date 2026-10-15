# Worker: handing off a phase mid-way

A phase worker gets a fresh session, not an unlimited one. A phase that turns
out larger than the planner thought (a wide refactor, a test suite that needs
more fixtures than expected, three rounds of a stubborn ablation) can fill it.
A worker that fills its context doesn't stop. It degrades: it re-reads files,
forgets which of its numbered steps actually landed, and reports a completion
it can no longer vouch for. The coordinator can't see that from outside,
because the report looks exactly like a good one.

So the phase may outlive the session that started it. The worker stops at a
safe point, and a fresh worker (the next **leg** of the same phase) finishes
it in the same worktree.

## Who decides what

Only the worker can read its own context, and only the worker knows whether
it is at a safe point. A worker three lines into a two-file edit, or holding
a reverted source file inside an ablation check, must not be interrupted. So
the worker decides *when* to stop.

The coordinator decides *what happens next*: it owns the run, the task graph
and worker placement. A worker never launches its own successor. A successor
started outside the coordinator's task graph sends a completion nobody is
waiting for, and the coordinator waits forever on a phase that is quietly
still running.

## Arming and checking

Arm before your first edit, when your phase file says to:

```bash
python3 <context-budget dir>/scripts/ctxcheck --arm --role worker --label "session N of /abs/plan.md"
```

Add `--gate P` only when the phase file gives one. Then check with a plain
`ctxcheck` (it reads the marker):

- after each numbered step lands;
- once more before starting any ablation check.

Exit 10, or a backstop warning, means you are over: **don't start the next
numbered step.** Exit 3 means this runtime can't be measured: say "no context
budget on this runtime" once and carry on.

## The over-budget procedure

**If the only work left is the commit itself** (tests green, the plan update
pending or done), make the commit and finish the phase normally. That is
cheaper than a whole new leg. Otherwise:

1. **Reach a safe point.** Between numbered steps, with the tree coherent.
   Never mid-edit. Never inside an ablation check with a source file still
   reverted or a backup still on disk: if you are mid-ablation, restore from
   the backup and confirm the tests are green first.
2. **Leave the edits uncommitted.** Your successor opens the same worktree, so
   the work is already there. Don't commit partial work, don't stash, don't
   revert. A phase ends with **one commit**, made by whichever leg finishes
   it, and that invariant is why a pause leaves the edits uncommitted.
3. **Save a patch backup** outside the repo, where it can't be committed, to
   the path your phase file names. If it names none, put it next to the
   continuation note as `phase-N-wip-leg<g>.patch` (`<g>` is your leg number,
   1 for the first worker):

   ```bash
   git add -A -N && git diff > <runs dir>/phase-N-wip-leg<g>.patch
   ```

   The `-N` (intent to add) is what makes new untracked files show up in the
   diff. Without it they are silently missing.
4. **Write the continuation note** at the path your phase file names (for
   run-plan, `<runs dir>/phase-N-continuation.md`). Write it for a worker that
   has this same phase prompt and no memory of your session:
   - which numbered steps are landed and which remain;
   - the output of `git status --porcelain`, and the patch backup's path;
   - which tests exist and their current pass/fail state;
   - which ablations are done and which are untouched;
   - **anything you learned that contradicts the phase prompt**: a drifted
     line number, a file that doesn't exist, a vendor fact that changed, a
     precedent that turned out wrong;
   - explicitly, what you have **not** verified.

   The last two are why the note exists. Everything you learned and didn't
   write down is lost.

   **If the note already exists, append to it** under a heading naming your
   leg (`## Leg 2`). You are leg 2 or later, and the earlier legs' findings
   still matter to the next one.
5. **Disarm:** `ctxcheck --disarm`.
6. **Report** exactly as your phase file says (for run-plan, the Orca
   `worker_done` message), with:
   - `PHASE-STATUS: continuation-required` as the **first line** of the body,
     then the continuation note's path and what remains;
   - a subject saying the session paused at its context budget;
   - outcome **succeeded**, not failed.

   `succeeded` is deliberate. The session did exactly what it should have.
   `failed` would be false, and it counts against the task's failure limit.
   The `PHASE-STATUS` line, not the outcome flag, tells the coordinator the
   phase isn't finished.

Then stop. Don't start more work in this session.

## Finishing normally

When the phase completes, disarm as part of the finish sequence (after the
commit and the plan update), and report with `PHASE-STATUS: complete` as the
first line if your phase file asks for a status line.

## As a later leg

If your phase file points you at a continuation note, read the phase prompt,
then the note. The note supersedes the prompt wherever they disagree. Check
`git status` against the note's record before you edit anything: the
predecessor's edits should already be in the worktree. Arm again (your
session is new), and continue from the first step the note lists as
remaining.

## Runtime notes

- **Claude Code:** the backstop registers only when the context-budget skill
  is invoked. If you have the skill, invoke it before arming.
- **Codex:** the backstop needs `ctxcheck install-hook codex`, trusted in
  `/hooks`. Without it, only your own checks catch an overage.
- **OpenCode and Pi:** the backstop needs `ctxcheck install-hook opencode|pi`
  once; the warning is appended to the tool result. Neither is live-verified.
  Pi sends no usage right after a compaction, so the first tool call after one
  stays silent.
- **Cursor, Copilot and Antigravity:** these need both
  `ctxcheck install-statusline <runtime>` and `ctxcheck install-hook <runtime>`.
  None of the three backstops is live-verified. Antigravity's warning arrives
  before the next model call (`PreInvocation`), not after a tool call.
  Copilot is not an Orca worker agent, so it never runs as a phase worker.

Installing a backstop edits the user's agent configuration. A worker never
runs an installer. If there is no backstop, your boundary checks are the
whole defense, so don't skip them.
