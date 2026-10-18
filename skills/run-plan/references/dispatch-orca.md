# Dispatch backend: Orca orchestration

This file is the **only** place run-plan names its dispatch backend. To move to another
backend (herdr, plain tmux, …), write a sibling `dispatch-<name>.md` with the same section
headings — preamble text, completion paragraph, dispatch/wait/retry procedure — and nothing
in SKILL.md or plan-format.md changes.

It documents the loop **shape** and the pitfalls, for Orca 1.4.229 and later. The command
lines below were checked against `orca-ide` 1.4.229's `--help` and bundled guide on
2026-10-18; flags rot across releases, so the guide you load in §0 wins wherever the two
disagree. Commands are written `<orca> orchestration <verb>`, with `<orca>` resolved per §0.

## 0. Resolve the binary, load the real guide

Resolution order: `$ORCA_CLI_COMMAND` → `orca-dev` (if `$ORCA_DEV_REPO_ROOT` is set) →
`orca-ide` (Linux outside an Orca terminal) → `orca`. **Never invoke bare `orca` on Linux
without checking the earlier steps — it is the GNOME screen reader.**

Then, before touching any orchestration command, load the version-matched reference:

```
<orca> skills get orchestration
```

and follow it for exact flag spellings. That is the compact guide; the bundled references
stay out of your context until a gate needs one. The **recovery reference** this file
points at for failed, stopped and `outcome_unknown` attempts is loaded on demand with:

```
<orca> skills get orchestration --reference references/recovery-and-cleanup.md
```

Load it then, not up front: you are the session whose context has to last the whole run.
If the CLI rejects `--reference`, run `<orca> skills get orchestration --full` once and
read only that reference from it.

## 1. Run binding

One `<orca> orchestration run-create --objective "<plan title> — <abs plan path>" --json`
per plan run; record the run id in `run-state.json`. Only the terminal bound to the Run may
dispatch workers (anything else gets `consumer_fenced`). The binding is per-terminal and
can lapse (e.g. after a compaction): a `run_required` error ("No Run is bound") means
`<orca> orchestration run-use --id <run_id> --json` to re-bind — **never** create a second
Run for the same plan run.

On resume, reuse the recorded run id via `run-use`; create a new Run only if the old one is
gone.

**A plain `run-use` from another terminal moves the Run.** It rebinds the Run to the calling
terminal and fences the previous coordinator: that terminal's in-flight `check --wait`
returns `consumer_fenced`, and its later commands get `consumer_fenced` or `run_required`.
(This is from the 1.4.219 source; the guide documents fencing only for `--takeover-legacy`,
which run-plan never uses.) Two consequences:

- Never `run-use` a live run's id from a second terminal. It silently takes the run away
  from the coordinator that owns it.
- If **you** get `consumer_fenced`, another terminal holds the Run. Do not `run-use` back
  (that fences the other one); stop dispatching and tell the user. Coordinator succession
  (§9) uses this rebinding deliberately.

## 2. One task per leg, created lazily

A phase runs as one or more **legs**: leg 1 is its first worker, and each continuation
(SKILL.md failure machine) is the next leg. Every leg is its own Task, created just before
that leg's dispatch — never pre-built as a chain. A valid `worker_done` settles a Task to
`completed`, and a completed Task can't be retried, so a continuation needs a new Task
anyway. Creating lazily also keeps the DAG honest: phase N+1 never shows ready while phase
N is mid-continuation.

**The `--spec` is the delivered prompt, not a label.** `worker-start` injects the task's
spec (wrapped in Orca's lifecycle preamble) into the worker — there is no `--prompt` flag
anywhere in the dispatch surface, and no separate delivery step. Human labels go in
`--task-title` (task rows) and `--display-name` (worker rows):

```
<orca> orchestration task-create \
  --spec "Read <abs runs dir>/phase-N.txt and execute it exactly as written." \
  --task-title "Session N of M (leg g): <phase title>" \
  --display-name "Session N (leg g): <phase title>" \
  --deps '["<previous task id>"]' --json
```

`<previous task id>` is the Task this one follows: the previous leg's Task for a
continuation, otherwise the previous phase's last leg Task. The very first leg of the run
has no `--deps`. Assemble `phase-N.txt` for this leg **before** creating its Task (§4).

Specs and deps are **immutable** — `task-update` changes only status/result — so a task
created with a label in `--spec` cannot be repaired: create a replacement and retire the
orphan with `<orca> orchestration task-update --id <old id> --status failed --result '{"superseded_by": "<new id>"}' --json`.

For the same reason, never re-run a `task-create` whose response was lost (a transport
error with no receipt): the duplicate is permanent. Recover it per §3's lost-receipt rule.

The task list is external memory: a coordinator that lost context can rediscover where the
run stands from `<orca> orchestration task-list --brief --json` (specs capped at 160 characters; drop
`--brief` when you need a full spec) plus the plan's tags and the legs in
`run-state.json`. Record each leg's task id there as soon as `task-create` returns it.

## 3. Starting a worker

```
<orca> orchestration worker-start --task <task_id> --worktree current \
  --agent <run-config.agent> [--model <run-config.model> [--effort <run-config.effort>]] --json
```

- `--worktree current` = a fresh agent terminal in the existing checkout, no setup rerun —
  the "fresh session, same repo" semantic every phase wants. Run it from `run-config.cwd`.
- `--agent` takes any Orca agent id enabled on the worker server; `worker-start --help`
  lists them. Don't hardcode the list — it changes across Orca releases.
- The model is set **only** via `--model` (provider-opaque id, e.g. `opus`), and only when
  run-config has a `model` key. In 1.4.229, `--model` works for claude, codex, cursor,
  antigravity, muse and omp, and for opencode in an existing worktree (which
  `--worktree current` is) once the host has verified its CLI and the model; other
  agents take their model from their own config. `worker-start --help` has the current
  list. If `worker-start` rejects the id, re-resolve current ids via the loaded
  orchestration guide and tell the user — do not silently substitute.
- Reasoning effort is set **only** via `--effort`, and only when run-config has an
  `effort` key. It requires `--model`, so an `effort` without a `model` is ignored (log
  it). Claude, Codex, Cursor, Antigravity and Muse take it; omp and opencode don't.
- **Never hand-build the agent argv** (no `--terminal` + your own agent command
  line): a custom argv replaces Orca's launch defaults, the worker comes up sandboxed off
  the Orca socket, and a sandboxed worker can never send heartbeat or `worker_done` — under
  the long-wait doctrine that failure mode is invisible.

**Reading the receipt.** `worker-start` waits for the agent's turn to start, and exits 0
only for `ready`.

- **Exit 0 (`ready`).** Record `dispatchId` in the leg's `run-state.json` entry and read
  `turnStart`, which drives first contact (§5). Compare `launch.requested` with
  `launch.effective`. If the effective agent, model or effort is not what run-config asked
  for, log it and tell the user. Don't re-dispatch over it.
- **Exit 1, `state: failed`.** Read `stage`, `failedStage` and `residualResources`, and run
  only the recovery commands the receipt prints (`nextCommands`). A start that failed
  before its agent was ready still owns the terminal it created. Release that terminal with
  `worker-release`, never by hand. This counts as a failed attempt (§7). Never blind-retry.
- **Exit 1, `state: outcome_unknown`.** Orca couldn't observe the turn start
  (`turnStart: unobserved`; the worker reads `start_unknown`), and the Task is now
  `blocked`. Load the recovery reference and inspect before acting:
  `<orca> orchestration worker-show --dispatch <id> --json` and
  `<orca> orchestration worker-read --dispatch <id> --source auto --limit 20 --json`.
  Then choose explicitly: `<orca> orchestration worker-stop --dispatch <id> --json` when the
  agent terminal is proven, or `<orca> orchestration worker-abandon --dispatch <id> --json`
  when it isn't. Treat it as a failed attempt and retry per §7.
- **No receipt at all** (the response was lost to a transport error, so you hold no
  dispatch id). Never re-run the command blind: a second `worker-start` puts two editors in
  one checkout. If the error reports a request id, ask Orca what happened:
  `<orca> orchestration request-show --request <request_id> --json`. `completed` → read its
  recorded receipt and carry on from it. `pending` → replay the **exact** original command
  with `--retry-request <request_id>` appended, which joins the original instead of starting
  another. `absent` (or no request id) → proves nothing; inspect with
  `<orca> orchestration worker-list --run <run_id> --json` and `task-list` before deciding.
  The same rule covers every orchestration mutation, `task-create` included.

## 4. Delivering the prompt

Delivery happens **at `worker-start`, by Orca**: the task's spec, wrapped in the lifecycle
preamble, is injected into the fresh agent. The one-line spec set at task-create
(§2) — `Read <abs path to phase-N.txt> and execute it exactly as written.` — is therefore
the entire delivered prompt, and there is nothing for the coordinator to send afterward.
Assemble `phase-N.txt` **before** `worker-start`, or the worker reads a missing file.

Never send the prompt body through the terminal — `terminal send` submits on **every
newline**, so any multi-line text arrives as a shredded stream of partial prompts. The full
wrapped prompt lives in the phase file (assembled per plan-format.md §7 with this file's
preamble and completion paragraphs).

## 5. First contact — prove the prompt landed

A worker whose prompt was swallowed looks identical to a healthy worker mid-phase, so
every dispatch needs proof that the prompt landed. The receipt's `turnStart` says which
proof you have:

- **`turnStart: observed`**: Orca saw the agent's turn start on the injected prompt. The
  prompt provably landed. Skip the heartbeat wait and go straight to §6. The worker still
  sends its first-contact heartbeat; you just don't wait for it.
- **`turnStart: permission` or `unsupported`**: Orca couldn't prove the turn started. With
  `permission` the agent stopped at a permission or trust prompt first; with `unsupported`
  this agent gives no turn signal. The worker's heartbeat (§8a) is the proof. So:
  1. Wait for it:
     `<orca> orchestration check --wait --types heartbeat,worker_done,escalation,question --timeout-ms 180000 --json`.
  2. On silence, look **exactly once**, with these two reads:
     `<orca> orchestration worker-show --dispatch <id> --json`, then
     `<orca> orchestration worker-read --dispatch <id> --source auto --limit 20 --json`.
     - Work visibly in progress → the heartbeat was skipped, not the prompt lost; go to
       §6. Re-delivering now would double-inject.
     - A permission or trust dialog → only a person can clear it. `worker-show`'s
       `observation.agentWait`, when non-null, names it and the evidence (hook, prompt
       text or title); otherwise it shows in the read. Tell the user, and keep waiting
       after they do. Never re-deliver over a dialog.
     - Empty composer or startup placeholder → the prompt was swallowed. Re-deliver once:
       `<orca> orchestration worker-stop --dispatch <id> --json`, then
       `<orca> orchestration worker-start --task <t> --retry-of <id> --worktree current --agent <a> [--model <m> [--effort <e>]] --json`.
       `--retry-of` accepts only a proven failed or stopped attempt, which is why the
       stop comes first. Orca may count the stopped attempt toward its three-failure
       breaker. **Never `terminal send` the one-liner instead**: that delivers text
       without the lifecycle preamble, so the worker could never report done.
  3. **Never a third delivery.** Still silent after the one re-delivery → mark the phase
     `[blocked]` with what was observed, release, stop.

`outcome_unknown` never reaches this section. It is a nonzero exit, handled in §3.

## 6. Waiting

Rolling waits, re-issued in a loop until a terminal event:

```
<orca> orchestration check --wait --types worker_done,escalation,question --timeout-ms 900000 --json
```

- `--wait` emits a `_keepalive` line on stderr every 15 s — the wait itself proves the
  channel is alive. A timeout or `{count: 0}` is a checkpoint, not a failure; quiet is not
  dead. `--types` sets only what wakes the wait; heartbeats still arrive in the batch.
- A `check` returns the Run's oldest unacknowledged FIFO delivery (up to 50 messages) and
  **replays that same batch until you acknowledge it**. Process everything in it, then pass
  `--ack <delivery_id>` on the next `check`.
- **After three consecutive empty waits**, stop waiting blindly and enumerate:
  `<orca> orchestration worker-list --run <run_id> --include-remote --json`. On this
  dispatch's row, act on `projection.liveness`, `projection.attention` (its `categories`
  and `requiresAction`) and the literal `projection.nextAction` argv. A `none` nextAction
  has no argv: read `liveness.reason` and keep waiting. Reset the empty-wait count after
  enumerating.
- **A worker parked on a human-only prompt** (a permission or trust dialog mid-phase) is
  healthy, not failed, but it waits forever. When `attention` asks for action, or once per
  enumeration while the worker is still quiet, run
  `<orca> orchestration worker-show --dispatch <id> --json`. A non-null
  `observation.agentWait` names the prompt and its evidence. Tell the user what it is
  waiting on and keep waiting. Null means Orca looked and found no wait; an **absent**
  field means it never looked, which proves nothing either way.
- **Absence authorizes nothing.** `unverifiable` liveness, a null `agentWait` or an
  unchanged output tail is absence, not proof. Leave the wait only on positive proof the
  agent stopped: `exited` liveness, or a transcript whose final agent turn sent no
  `worker_done`. Then load the recovery reference and choose `worker-stop` or
  `worker-abandon` explicitly; the attempt counts as failed (§7).
- **Never `terminal read` a worker.** Workers may be terminal-less chat sessions, and a TUI
  pane read buys kilobytes of repaint noise. If you need evidence, read once with
  `<orca> orchestration worker-read --dispatch <id> --source auto --limit 20 --json`, which
  uses the transcript when there is one.
- Only these end a phase: `worker_done`, `escalation`, or a proven-dead dispatch as above.
  No wall-clock kill.

## 7. Completion, release, retry

- Validate a `worker_done` against the leg's active dispatch id before acting on it; a late
  one from an earlier attempt is logged and ignored.
- Read the **first line** of the body: `PHASE-STATUS: complete` or
  `PHASE-STATUS: continuation-required`. The guide's three-sentence summary follows that
  line (§8b). A body with no status line is treated as complete. The `--outcome` flag is
  **not** the phase signal — a clean continuation pause reports `succeeded`.
- A valid `worker_done` settles both task and dispatch — never also
  `task-update --status completed`.
- `<orca> orchestration worker-release --dispatch <id> --json` on **every** settled dispatch
  (complete, continuation, failed, blocked) before moving on; it closes only that dispatch's
  terminal and is safe to repeat. Use `worker-retain` only if the user asks to keep a session
  open.
- **A `failed` outcome retries on the same leg Task.** Summarize the failure into a
  regenerated `phase-N.txt`; the Task's spec already points at that path, so overwrite it in
  place. Then start a fresh dispatch with placement and agent/model/effort restated,
  because a retry never inherits placement:
  `<orca> orchestration worker-start --task <t> --retry-of <dispatch_id> --worktree current --agent <a> [--model <m> [--effort <e>]] --json`.
- **Two failure counters, and they count different things.** Orca circuit-breaks a task
  after 3 consecutive failed **starts** (failed `worker-start` attempts, counted across
  `--retry-of`). A worker's own `--outcome failed` report does **not** count toward it. So
  run-plan's `max_consecutive_failures` (default 3) is the only bound on failures workers
  report, and it counts every failed attempt. If Orca refuses a `--retry-of` with
  `task_not_startable` because the task is circuit-broken, mark the phase `[blocked]` and
  stop. Never route around the breaker with a new Task or Run.
- **A continuation is a new leg, never a retry**: release, then create the next leg's Task
  (§2) depending on this one, and start it (§3).
- Answer a worker's `ask` with `<orca> orchestration reply --id <msg_id> --body "<answer>" --json`.
  Send mid-phase follow-ups with `<orca> orchestration send --to dispatch:<id> ...` — never by
  typing into its terminal. Workers pick them up at their checkpoints (§8b).
- **Before ending the run** (any exit state), enumerate leftovers with
  `<orca> orchestration worker-list --run <run_id> --terminal-state reclaimable --json` and
  release each one listed. Rows page at 100: while `page.hasMore`, continue with
  `--cursor <page.nextCursor>`. The run isn't over until that list is empty.

## 8. Canonical worker-facing text

### 8a. First-contact preamble (opens every phase file)

> **Before anything else — before you read a single file** — tell the coordinator you have
> this prompt. Send a `heartbeat` exactly as the lifecycle preamble Orca injected into
> your terminal with your dispatch shows it, with `--phase starting` and the subject
> `session <n> started`. Copy
> the preamble's own command: it supplies your terminal handle, task id and dispatch id,
> and any other flag it carries (older Orca hosts add a dispatch capability). Never
> rebuild those arguments yourself. This one command is what tells the coordinator the dispatch
> landed rather than being swallowed at startup, and until it arrives the coordinator
> cannot tell you apart from a worker that never woke up.

### 8b. Completion paragraph (closes every phase file)

> **Coordinator follow-ups** reach you only when you look. Check for them with the
> preamble's `check --terminal <handle> --json` command at natural
> checkpoints (before you start a new file, after a test run) and once more immediately
> before you send `worker_done`, so a redirect lands before the phase settles. If any
> orchestration command answers `consumer_fenced`, your dispatch was taken over: stop work
> and do not send `worker_done`.
>
> When finished, send your completion via the Orca orchestration CLI: load the
> `orchestration` skill and send a `worker_done` message with your injected task id and
> dispatch id, `--outcome succeeded` (or `failed`), and the list of files you modified. Make
> `PHASE-STATUS: complete` the **first line** of the body, then the three-sentence summary
> the preamble asks for: what changed (with the commit SHA), what you found (with test
> results), and what remains or what the next session must know. Send it exactly once. If
> you are blocked on a decision only the coordinator can make, use the orchestration `ask`
> flow and wait for the reply — do not guess, and do not go idle.
>
> If instead you stopped at your context budget (the context-budget paragraphs above, and
> context-budget `references/worker.md`), send the same `worker_done` with
> `--outcome succeeded` — the session did its job by stopping cleanly — but make the subject
> `session <n> paused at context budget` and the **first line of the body**
> `PHASE-STATUS: continuation-required`, then name the continuation note by path and stop.
> Do not use `--outcome failed`: nothing failed, and the coordinator would retry the phase
> from scratch and count it toward blocking the phase.
>
> Never signal completion by writing a sentinel file or by going idle — `worker_done` is
> the only completion signal the coordinator trusts.

## 9. Coordinator succession

The backend half of `coordinator-handoff.md`: that file says when and why, this section
says which commands. Checked against `orca-ide` 1.4.229's `--help` on 2026-10-18 (first
written against 1.4.219 and its app bundle). Every `terminal` verb below takes an explicit `--terminal <handle>`:
without one, Orca targets the **active terminal of the cwd's worktree**, which may be some
other agent's pane, not yours.

**Am I inside an Orca terminal?** (handoff step 1.) Yes when `$ORCA_TERMINAL_HANDLE` is
non-empty **and** this succeeds with `ok: true` and the same handle in
`result.terminal.handle`:

```
<orca> terminal show --terminal "$ORCA_TERMINAL_HANDLE" --json
```

A `terminal_handle_stale` error, a missing variable or a mismatched handle means no: take
the handoff fallback. Never probe with a bare `terminal show` (it answers for the worktree's
active terminal, which can be another agent's).

**Settle the inbox before the note** (handoff step 2). Acknowledge the batch you already
processed (the one carrying the last `worker_done`) with a non-waiting check, and process
whatever it returns. At a boundary with no worker in flight that is only stragglers, such
as late heartbeats. Repeat until a check returns `count: 0`:

```
<orca> orchestration check --ack <delivery_id> --json
```

After the note is written, never pass `--ack` again (handoff step 4). An unacknowledged
batch replays to whoever holds the Run next. This settling step is also what lets the
takeover wait below block instead of returning a replay at once.

**Launch the successor** (handoff step 6), from `run-config.cwd`. `--worktree active` means
the Orca worktree enclosing the shell's cwd:

```
<orca> terminal create --worktree active --title "run-plan coordinator gen <G+1>" --command "<shell_command>" --json
```

- `<shell_command>` is `ctxcheck launch-command --json`'s `shell_command`, verbatim. Orca
  types it into a login shell, so the agent's launcher defaults don't apply. That is why
  the command is your own detected argv and never a bare agent name or a hand-built one.
- `terminal create --help` points agent starts at `<orca> agent launch`. Not here:
  `agent launch` applies Orca's launcher defaults rather than your own argv, and it may
  open the successor as a chat, per the user's Chat UI setting, instead of a terminal.
- Record `result.terminal.handle` as `<successor>` in `run-log.md` and in the
  `handoffs[]` entry (`successor_terminal`).

Wait for the agent's interface. A line typed into a booting TUI goes to a bare tty and is
discarded when the TUI takes the screen:

```
<orca> terminal wait --terminal <successor> --for tui-idle --timeout-ms 60000 --json
```

A timeout still prints a normal result, so read `result.wait.satisfied`, not the exit
code. On `false`, wait once more with `--timeout-ms 180000`. Still `false` → handoff
step 8 (the one diagnostic read below).

Then send **one line** (handoff step 6 has the text; it has no newline in it):

```
<orca> terminal send --terminal <successor> --text "<one line>" --enter --wait-submit 30 --json
```

- **Submitted**: `result.send.prompt.stages` contains `turn_started`.
- `result.send.accepted: false` → read `send.refusedReason`, then handoff step 8.
- `send.prompt.observation: permission` → a permission or trust dialog. That is handoff
  step 8's dialog branch: tell the user, and don't re-send.
- Only `input_accepted` (the 30 s ran out) → nothing was re-sent and nothing is lost.
  Repeat once as `--retry-request <send.prompt.requestId> --wait-submit 60`, never as a
  fresh `--text` send, which could submit the line twice.

**Confirm the takeover** (handoff step 7). The successor rebinds with
`<orca> orchestration run-use --id <run_id> --json` from its own terminal, the deliberate
use of the rebinding §1 warns about. That fences you. Wait for it **without `--ack`**:

```
<orca> orchestration check --wait --types worker_done,escalation,question --timeout-ms 600000 --json
```

- **`consumer_fenced`** (any of "This mailbox consumer has been replaced", "This
  coordinator terminal is no longer bound to Run …") or **`run_required`** ("No Run is
  bound") → the takeover is confirmed. Re-read `run-state.json` for `takeover` to log the
  generation, but don't wait for it.
- **A batch comes back** (a straggler arrived) → don't process it and don't ack it; it
  belongs to the successor. Re-read `run-state.json`: `takeover.generation == G+1` →
  confirmed. Otherwise run `<orca> orchestration run-current --json` (non-consuming): a
  null `run`, or a different one, means you were unbound → confirmed. Neither → pause
  without consuming, `<orca> terminal wait --terminal <successor> --for exit --timeout-ms 120000 --json`
  (a bounded pause: the successor isn't expected to exit), and repeat these two reads.
  Stop once 600000 ms have passed since the send.
- **The wait times out** with neither → handoff step 8.

**The one diagnostic read** (handoff step 8). Exactly once, bounded, for diagnosis only,
and only of the successor's terminal, never a worker's (§6):

```
<orca> terminal read --terminal <successor> --screen --limit 40 --json
```

Decide from it per handoff step 8: a dialog → tell the user; an empty composer on the
agent's startup placeholder → the one re-send (the `terminal send` above, fresh, with
`--wait-submit 30`), then the takeover wait once more.

**If the succession failed**, leave the successor's terminal open and name `<successor>`
to the user rather than closing it yourself. With `takeover_pending` cleared, a successor
that wakes later stands down without touching the Run.
