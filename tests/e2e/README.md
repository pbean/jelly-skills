# Live smoke test: context budgets under run-plan

The unit suite (`python3 -m unittest discover -s tests -v`) covers ctxcheck offline. It
can't cover the parts that only happen inside a real Orca run:

- a phase worker that goes over budget and stops, and a **continuation leg** that runs as a
  new Task;
- a coordinator that goes over budget, launches a **successor coordinator** in a new
  terminal, confirms the takeover, and stops;
- a successor that rebinds the Run, finishes the plan, and disarms.

This kit drives exactly that path with a 2-phase dummy plan, [`smoke-plan.md`](smoke-plan.md),
whose gates are both 1%. It is a manual test: it starts real Orca workers and costs a few
real agent sessions. Expect it to take 10-20 minutes.

The commands below work in bash, zsh and fish. Run them from the jelly-skills checkout.

## 1. Reinstall the skills

The run uses the **installed** copies, not this checkout, so install the current tree first:

```bash
npx skills add ./ --skill '*' -g -a claude-code -a codex -y
```

Add `-a` for any other agent you use (see the main [README](../../README.md#install)). Start
new agent sessions afterwards: running sessions keep the skills they loaded.

## 2. Create the scratch repo and the plan copy

The plan's workers flip tags and append outcome records in the plan file, so run a copy
that lives outside any git repo. Its run state then lands next to it, in
`~/.claude/plans/runs/jelly-smoke/`.

```bash
mkdir -p ~/jelly-smoke ~/.claude/plans
git -C ~/jelly-smoke init -b main
echo '# jelly-smoke' > ~/jelly-smoke/README.md
git -C ~/jelly-smoke add -A
git -C ~/jelly-smoke commit -m "init"
sed "s#@SCRATCH@#$HOME/jelly-smoke#g" tests/e2e/smoke-plan.md > ~/.claude/plans/jelly-smoke.md
orca-ide repo add --path ~/jelly-smoke
```

Workers start with `worker-start --worktree current`, so the scratch repo must be an Orca
repo (the last line). On Linux, always call the Orca CLI as `orca-ide`: bare `orca` is the
GNOME screen reader.

## 3. Run it

1. In Orca, open a terminal in the `jelly-smoke` worktree.
2. Start Claude Code there with the permission posture you want the successor to have.
   The successor coordinator is launched with **exactly your command line** (`ctxcheck
   launch-command`), so an unattended run needs something like
   `claude --dangerously-skip-permissions`. Without it, the successor stops at a permission
   prompt, and the predecessor tells you so and does not re-send.
3. Run:

   ```
   /run-plan /home/<you>/.claude/plans/jelly-smoke.md
   ```

   Use the absolute path. Don't touch the run until it reports that it is finished, apart
   from answering a permission prompt.

## 4. What you should see

In order:

1. **Coordinator generation 1 arms itself** at a 1% gate (`--role coordinator --gate 1`)
   and dispatches phase 1, leg 1, as a Task titled like "Session 1 of 2 (leg 1)".
2. **The leg-1 worker stops after step 1.** Its `worker_done` body starts with
   `PHASE-STATUS: continuation-required`. In `~/.claude/plans/runs/jelly-smoke/` you find
   `phase-1-continuation.md` and `phase-1-wip-leg1.patch`. In the scratch repo,
   `notes.txt` has one line and is **uncommitted**.
3. **Generation 1 hands off.** Its boundary check exits 10 with phase 1's second leg and
   phase 2 still left. It writes `coordinator-handoff.md` in the runs dir, and
   `run-state.json` shows `takeover_pending: 2` and a `handoffs[]` entry for generation 1.
4. **A successor terminal appears** in the same worktree, titled
   "run-plan coordinator gen 2", and receives one line of prompt.
5. **The predecessor confirms the takeover and stops.** It reports in one paragraph
   (generation 2, the percentage, the phase, the terminal) that the takeover is confirmed,
   either because it was fenced (`consumer_fenced` / `run_required`) or because
   `run-state.json` shows the successor's `takeover` record. Then it ends its turn and
   dispatches nothing more.
6. **Generation 2 takes over.** `run-state.json` shows
   `takeover: {generation: 2, at: …}`, `coordinator_generation: 2` and
   `takeover_pending: null`. It does not ask you to confirm the run-config again.
7. **Phase 1 continues as a new Task.** Leg 2 is a second Task ("Session 1 of 2 (leg 2)")
   that depends on leg 1's completed Task. Phase 1's `legs` in `run-state.json` has two
   entries. Leg 2 reads the continuation note, adds step 2, and makes the phase's only
   commit, `smoke: phase 1`.
8. **Generation 2 finishes instead of handing off again.** Its next check is also over 1%,
   but only the last phase is left, so it logs the overage in `run-log.md` and dispatches
   phase 2. That worker has only its commit left after step 1, so it finishes in one leg
   (`smoke: phase 2`).
9. **The run ends cleanly.** Both headings in the plan copy end in `[done]` with outcome
   records, generation 2 disarms and prints the end-of-run summary, and
   `git -C ~/jelly-smoke log --oneline` shows `init`, `smoke: phase 1` and `smoke: phase 2`.

Useful checks while it runs:

```bash
cat ~/.claude/plans/runs/jelly-smoke/run-state.json
cat ~/.claude/plans/runs/jelly-smoke/run-log.md
orca-ide orchestration task-list --run <run_id> --brief --json
ls ~/.local/state/jelly-skills/ctxcheck/markers/
```

The run id is in `run-state.json`. If you set `CTXCHECK_HOME` or `XDG_STATE_HOME`, the
markers dir is under that instead.

### If something else happens

- **The run blocks phase 1 with "this phase repeatedly exceeds one session".** A leg
  stopped without landing a step. Read the continuation note and the leg's
  `worker_done`; each leg should land one numbered step before its first check.
- **No successor terminal appears.** Read generation 1's last message: outside an Orca
  terminal, or when `launch-command` fails, it stops and leaves `coordinator-handoff.md`
  for a manual successor. Open a fresh session in the worktree and run the same
  `/run-plan` line; it confirms once that the old coordinator has stopped, then takes over.
- **The successor sits at a trust or permission dialog.** Answer it. The predecessor
  doesn't re-send in that case.

The plain `run-use` fencing that step 5 relies on comes from Orca 1.4.219's bundle and is
not in the orchestration guide. If the predecessor confirms only through the `takeover`
record, that is still a pass. Note it, though: it means fencing behaves differently than
documented in `skills/run-plan/references/dispatch-orca.md` §9.

## 5. Clean up

1. **Release leftover worker terminals.** List what the run still holds, then release each
   settled worker by its dispatch id:

   ```bash
   orca-ide orchestration worker-list --run <run_id> --terminal-state reclaimable --json
   orca-ide orchestration worker-release --dispatch <dispatch_id> --json
   ```

   Close the "run-plan coordinator gen 2" terminal and your own coordinator terminal when
   you are done reading them.
2. **Remove leftover markers.** Both coordinators and every worker disarm on exit, so the
   markers dir should hold nothing from this run. Delete any marker whose `label` names the
   smoke plan:

   ```bash
   grep -l jelly-smoke ~/.local/state/jelly-skills/ctxcheck/markers/*.json
   ```

   Markers left by an interrupted session are also reaped automatically after 7 days.
3. **Delete the scratch repo, the plan copy and its run state.** Remove the `jelly-smoke`
   repo from Orca in the app first, then:

   ```bash
   rm -rf ~/jelly-smoke ~/.claude/plans/jelly-smoke.md ~/.claude/plans/runs/jelly-smoke
   ```
