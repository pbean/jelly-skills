# Live smoke test: context budgets under run-plan

The unit suite (`python3 -m unittest discover -s tests -v`) covers ctxcheck offline. It
can't cover the parts that only happen inside a real Orca run:

- a phase worker that goes over budget and stops, and a **continuation leg** that runs as a
  new Task;
- a coordinator that goes over budget, launches a **successor coordinator** in a new
  terminal, confirms the takeover, and stops;
- a successor that rebinds the Run, goes over budget itself and hands off to a **third
  generation**;
- a last generation that finishes the plan instead of handing off again, and disarms.

This kit drives exactly that path with a 3-phase dummy plan, [`smoke-plan.md`](smoke-plan.md),
whose gates are both 1%. Nothing has to fill up: every session is over 1% from its first
turn. It is a manual test: it starts real Orca workers and costs three coordinator and four
worker sessions, all short. Expect it to take 5-15 minutes. Sections 1-5 run it under
Orca; [section 6](#6-running-the-smoke-test-under-herdr) runs the same plan under herdr.

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

   A freshly created scratch repo also shows Claude Code's folder-trust dialog, whose
   default is **No, exit**. Pick "Yes, I trust this folder". Successors start in the same
   folder, so they skip it.
3. Run:

   ```
   /run-plan /home/<you>/.claude/plans/jelly-smoke.md
   ```

   Use the absolute path. Don't touch the run until it reports that it is finished, apart
   from answering a permission prompt.

## 4. What you should see

In order:

1. **Coordinator generation 1 arms itself** at a 1% gate (`--role coordinator --gate 1`)
   and dispatches phase 1, leg 1, as a Task titled like "Session 1 of 3 (leg 1)".
2. **The leg-1 worker stops after step 1.** Its `worker_done` body starts with
   `PHASE-STATUS: continuation-required`. In `~/.claude/plans/runs/jelly-smoke/` you find
   `phase-1-continuation.md` and `phase-1-wip-leg1.patch`. In the scratch repo,
   `notes.txt` has one line and is **uncommitted**.
3. **Generation 1 hands off.** Its boundary check exits 10 with phase 1's second leg,
   phase 2 and phase 3 still left. It writes `coordinator-handoff.md` in the runs dir, and
   `run-state.json` shows `takeover_pending: 2` and a `handoffs[]` entry for generation 1.
4. **A successor terminal appears** in the same worktree, titled
   "run-plan coordinator gen 2", and receives one line of prompt.
5. **The predecessor confirms the takeover and stops.** Its takeover wait fails with
   `consumer_fenced` ("This coordinator terminal is no longer bound to Run …"), or
   `run-state.json` shows the successor's `takeover` record. It reports in one paragraph
   (generation 2, the percentage, the phase, the terminal), ends its turn and dispatches
   nothing more.
6. **Generation 2 takes over.** `run-state.json` shows
   `takeover: {generation: 2, at: …}`, `coordinator_generation: 2` and
   `takeover_pending: null`, and the `run_id` is unchanged. It does not ask you to confirm
   the run-config again.
7. **Phase 1 continues as a new Task, unchecked.** Generation 2 dispatches leg 2 without a
   budget check, because generation 1 already checked that boundary. Leg 2 is a second
   Task ("Session 1 of 3 (leg 2)") that depends on leg 1's completed Task. Phase 1's `legs`
   in `run-state.json` has two entries. Leg 2 reads the continuation note, adds step 2, and
   makes the phase's only commit, `smoke: phase 1`.
8. **Generation 2 hands off to generation 3.** Its first check, after phase 1, exits 10
   with phases 2 and 3 left. Steps 3-6 repeat one generation up: a
   "run-plan coordinator gen 3" terminal, a second `handoffs[]` entry, generation 2
   fenced, and `takeover: {generation: 3, …}`.
9. **Generation 3 finishes instead of handing off again.** It dispatches phase 2
   unchecked (`smoke: phase 2`). Its next check is over 1% too, but only the last phase is
   left, so it logs the overage in `run-log.md` and dispatches phase 3 (`smoke: phase 3`).
   Both of those workers have only their commit left after step 1, so each finishes in one
   leg.
10. **The run ends cleanly.** All three headings in the plan copy end in `[done]` with
    outcome records, `handoffs[]` has two entries, generation 3 disarms and prints the
    end-of-run summary, and `git -C ~/jelly-smoke log --oneline` shows `init` and
    `smoke: phase 1` to `smoke: phase 3`.

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

- **Generations keep handing off without dispatching.** A successor checked the boundary
  it resumed at instead of dispatching first (`coordinator-handoff.md`, "Taking over" step
  7). Stop the run; that's a skill bug.
- **The run blocks phase 1 with "this phase repeatedly exceeds one session".** A leg
  stopped without landing a step. Read the continuation note and the leg's
  `worker_done`; each leg should land one numbered step before its first check.
- **No successor terminal appears.** Read generation 1's last message: outside an Orca
  terminal, or when `launch-command` fails, it stops and leaves `coordinator-handoff.md`
  for a manual successor. Open a fresh session in the worktree and run the same
  `/run-plan` line; it confirms once that the old coordinator has stopped, then takes over.
- **The successor sits at a trust or permission dialog.** Answer it. The predecessor
  doesn't re-send in that case.

The plain `run-use` fencing that step 5 relies on is not in Orca's orchestration guide.
It was first verified against a real Run on Orca 1.4.229 (2026-10-18). If a predecessor
confirms only through the `takeover` record, that is still a pass. Note it, though: it
means fencing changed from what `skills/run-plan/references/dispatch-orca.md` §9 documents.

## 5. Clean up

1. **Release leftover worker terminals.** List what the run still holds, then release each
   settled worker by its dispatch id:

   ```bash
   orca-ide orchestration worker-list --run <run_id> --terminal-state reclaimable --json
   orca-ide orchestration worker-release --dispatch <dispatch_id> --json
   ```

   Close the "run-plan coordinator gen 2" and "gen 3" terminals and your own coordinator terminal when
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

## 6. Running the smoke test under herdr

The same `smoke-plan.md` drives run-plan's herdr backend
(`skills/run-plan/references/dispatch-herdr.md`). It is just as manual and costs the same
real sessions: three coordinators and four workers. If you ran the Orca variant first, do
its clean-up (section 5) first: both variants use the same plan copy and runs dir.

The herdr backend was first live-validated by this run on 2026-10-21 (herdr 0.9.5, fish
4.9, Claude Code 2.1.306). That run found two pieces of drift, both since fixed in
`dispatch-herdr.md`: a fresh tab's shell is briefly busy, so `agent start` answered
`agent_pane_busy` (§3 now waits for the shell to settle), and `pane current` answers
`type: "pane_current"`, not `pane_info` (§9). Re-run it after a herdr upgrade and watch
for the checks at the end of this section.

### Set up

1. Reinstall the skills as in section 1. You need herdr 0.9.5 or newer (`herdr --version`).
   Run `herdr integration status`: the worker kind (`claude`) should show as installed and
   current. If it doesn't, `herdr integration install claude` gives herdr reliable state
   for the worker. run-plan only warns about this; it never installs one.
2. Create the scratch repo as in section 2, **without** `orca-ide repo add`. Make the
   plan copy with two extra run-config lines, `backend: herdr` and
   `agent_args: --dangerously-skip-permissions`:

   ```bash
   awk -v scratch="$HOME/jelly-smoke" '{ gsub("@SCRATCH@", scratch) } /^agent: claude$/ { print "backend: herdr"; print; print "agent_args: --dangerously-skip-permissions"; next } 1' tests/e2e/smoke-plan.md > ~/.claude/plans/jelly-smoke.md
   ```

   herdr launches each worker with no flags but the model and `agent_args`, so without
   that line every worker stops at its first permission prompt.

### Run it

1. Open a herdr pane and `cd ~/jelly-smoke`. `echo $HERDR_ENV` must print `1`: the
   coordinator refuses to drive herdr from outside it.
2. Start Claude Code there with the posture the successor should have, as in section 3
   (`claude --dangerously-skip-permissions`), and answer the folder-trust dialog.
3. Run `/run-plan /home/<you>/.claude/plans/jelly-smoke.md`, and leave the run alone
   until it reports that it is finished.

### What you should see

The order of events is the same as in section 4. What differs is how herdr shows them:

- **The run binds to your pane.** `run-state.json` shows `backend: herdr`, a `run_id`
  like `rp3f9a2c71`, and `owner.pane_id` equal to your pane's `$HERDR_PANE_ID`.
- **Each leg attempt is a tab of its own.** A tab labelled like
  "S1 leg 1: <phase title>" opens without taking focus and runs the worker, named
  `<run_id>-s1-l1-a1`. It closes when the attempt settles. Phase 1 opens two (legs 1 and
  2), and phases 2 and 3 one each.
- **A result file per attempt.** The runs dir gains `phase-1-l1-a1.result.md`, whose
  first line is `PHASE-STATUS: continuation-required`, then `phase-1-l2-a1.result.md`,
  `phase-2-l1-a1.result.md` and `phase-3-l1-a1.result.md`, each starting
  `PHASE-STATUS: complete`. There is no `worker_done` message: these files are the
  completion reports.
- **A successor tab per generation.** Each handoff opens a "run-plan coordinator gen 2"
  (later "gen 3") tab that receives one line of prompt. There is no `consumer_fenced` here:
  the predecessor confirms the takeover only when `run-state.json` shows
  `takeover.generation` at the new generation, and `owner.pane_id` moves to the
  successor's pane.
- **The run ends with no leg tabs left.** The last generation closes every leg tab still
  open before it reports. The coordinator tabs stay open for you to read.

Useful checks while it runs, from another herdr pane:

```bash
cat ~/.claude/plans/runs/jelly-smoke/run-state.json
ls ~/.claude/plans/runs/jelly-smoke/
herdr agent list
herdr tab list
```

### First-run checks

These held on 2026-10-21. After a herdr upgrade, confirm them again and report any that
differ. Each difference is drift in `dispatch-herdr.md`:

- The delivery `herdr agent prompt … --wait --until working --until blocked` returns with
  `agent_status: "working"` (§5), not `agent_prompt_stalled`.
- `herdr tab create` answers `.result.tab.tab_id` and `.result.root_pane.pane_id`, and
  `herdr agent start` answers `type: "agent_started"` with `.result.agent` and
  `.result.argv` (§3). `run-state.json`'s legs hold those ids. `run-log.md` shows no
  `agent_pane_busy`, or only a re-opened tab that did not count as a failed attempt.
- After a tab closes, `herdr agent get <that attempt's name>` answers `agent_not_found`
  (§7).
- At the first handoff, the successor tab opens, `herdr pane current --current` returns
  `type: "pane_current"` with `.result.pane.pane_id` (§9's probe), and the predecessor's
  takeover poll exits 0 rather
  than running out its 600 s.

### Clean up

Close the "run-plan coordinator gen 2" and "gen 3" tabs and your own pane when you are
done reading them, and check that `herdr tab list` shows no "S<N> leg" tab left. Then
remove leftover markers and the scratch files as in section 5, steps 2 and 3. There is no
Orca repo to remove.
