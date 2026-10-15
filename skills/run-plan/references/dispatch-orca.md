# Dispatch backend: Orca orchestration

This file is the **only** place run-plan names its dispatch backend. To move to another
backend (herdr, plain tmux, …), write a sibling `dispatch-<name>.md` with the same section
headings — preamble text, completion paragraph, dispatch/wait/retry procedure — and nothing
in SKILL.md or plan-format.md changes.

It documents the loop **shape** and the pitfalls. It deliberately does not pin exact CLI
flags, which rot across Orca releases.

## 0. Resolve the binary, load the real guide

Resolution order: `$ORCA_CLI_COMMAND` → `orca-dev` (if `$ORCA_DEV_REPO_ROOT` is set) →
`orca-ide` (Linux outside an Orca terminal) → `orca`. **Never invoke bare `orca` on Linux
without checking the earlier steps — it is the GNOME screen reader.**

Then, before touching any orchestration command, load the version-matched reference:

```
<orca> skills get orchestration
```

and follow it for exact flag spellings. This file's command sketches show shape only.

## 1. Run binding

One `orchestration run-create --objective "<plan title> — <abs plan path>" --json` per plan
run; record the run id in `run-state.json`. Only the terminal bound to the Run may dispatch
workers (anything else gets `consumer_fenced`). The binding is per-terminal and can lapse
(e.g. after a compaction): any `run_required` error means `run-use --id <run_id>` to
re-bind — **never** create a second Run for the same plan run.

On resume, reuse the recorded run id via `run-use`; create a new Run only if the old one is
gone.

## 2. One task per phase, chained

**The `--spec` is the delivered prompt, not a label.** `worker-start` injects the task's
spec (wrapped in Orca's lifecycle preamble) into the worker terminal — there is no
`--prompt` flag anywhere in the dispatch surface, and no separate delivery step. Human
labels go in `--task-title` (task rows) and `--display-name` (worker rows):

```
task-create \
  --spec "Read <abs runs dir>/phase-N.txt and execute it exactly as written." \
  --task-title "Session N of M: <phase title>" \
  --display-name "Session N: <phase title>" \
  --deps '["<prev task id>"]' --json
```

A DAG chained in phase order. The runs-dir path is deterministic at normalization time, so
tasks can be created up front; just assemble `phase-N.txt` before starting that phase's
worker (§4). Specs and deps are **immutable** — `task-update` changes only status/result —
so a task created with a label in `--spec` cannot be repaired: recreate it (and any
dependents, since deps can't be re-pointed) and retire the orphans with
`task-update --status failed --result '{"superseded_by": "<new id>"}'`.

The task list is external memory: a coordinator that lost context can rediscover where the
run stands from `task-list --ready` plus the plan's tags. Record task ids in
`run-state.json`.

## 3. Starting a worker

```
<orca> orchestration worker-start --task <task_id> --worktree current \
  --agent <run-config.agent> [--model <run-config.model>] --json
```

- `--worktree current` = a fresh agent terminal in the existing checkout, no setup rerun —
  the "fresh session, same repo" semantic every phase wants. Run it from `run-config.cwd`.
- `--agent` takes any Orca agent id enabled on the worker server; `worker-start --help`
  lists them. Don't hardcode the list — it changes across Orca releases.
- The model is set **only** via `--model` (provider-opaque id, e.g. `opus`), and only when
  run-config has a `model` key — some agents (e.g. `opencode`) take their model from their
  own config and reject `--model`. Never pass `--effort` unless the user asks. If
  `worker-start` rejects the id, re-resolve current ids via the loaded orchestration guide
  and tell the user — do not silently substitute.
- **Never hand-build the agent argv** (no `--terminal` + your own agent command
  line): a custom argv replaces Orca's launch defaults, the worker comes up sandboxed off
  the Orca socket, and a sandboxed worker can never send heartbeat or `worker_done` — under
  the long-wait doctrine that failure mode is invisible.
- `worker-start` exits 0 only for `ready`. On a nonzero exit read `stage`/`failedStage`/
  `residualResources` and follow the recovery commands it prints; never blind-retry.

## 4. Delivering the prompt

Delivery happens **at `worker-start`, by Orca**: the task's spec, wrapped in the lifecycle
preamble, is injected into the fresh agent terminal. The one-line spec set at task-create
(§2) — `Read <abs path to phase-N.txt> and execute it exactly as written.` — is therefore
the entire delivered prompt, and there is nothing for the coordinator to send afterward.
Assemble `phase-N.txt` **before** `worker-start`, or the worker reads a missing file.

Never send the prompt body through the terminal — `terminal send` submits on **every
newline**, so any multi-line text arrives as a shredded stream of partial prompts. The full
wrapped prompt lives in the phase file (assembled per plan-format.md §7 with this file's
preamble and completion paragraphs).

## 5. First contact — the prompt is not delivered until the worker says so

Orca reports a dispatch `accepted` the moment it writes the prompt to the terminal. If the
agent's TUI was still booting, the bytes landed on a bare tty and were discarded — and that
worker looks identical to a healthy worker mid-phase. So:

1. Every phase file **opens** with the first-contact preamble (§8a below).
2. Immediately after `worker-start`:
   `check --wait --types heartbeat,worker_done,escalation,question --timeout-ms 180000`.
3. On silence, look **exactly once**: `worker-read --dispatch <id> --limit 1`.
   - Work visibly in progress → the heartbeat was skipped, not the prompt lost; go back to
     long waits. Re-delivering now would double-inject.
   - Empty composer / startup placeholder → the prompt was swallowed. Re-deliver into the
     terminal that is now up:
     `worker-start --task <t> --terminal <handle> --retry-of <dispatch_id> --json`
     (handle from `worker-show`) — this re-injects the same task spec plus preamble.
     **Never `terminal send` the one-liner instead** — that delivers text without the
     lifecycle preamble, so the worker could never report done.
4. **Never a third delivery.** Still silent after the one retry → mark the phase
   `[blocked]` with what was observed, release, stop.

## 6. Waiting

Rolling waits, re-issued in a loop until a terminal event:

```
check --wait --types worker_done,escalation,question --timeout-ms 540000 --json
```

- `--wait` emits a keepalive every 15 s — the wait itself proves the channel is alive. A
  timeout or `{count: 0}` is a checkpoint, not a failure; quiet is not dead.
- **Never `terminal read` a worker's pane in a wait loop** — a TUI redraws char-by-char, so
  each read buys kilobytes of repaint noise and zero signal. `worker-read` is for the
  single first-contact check only.
- A `check` returns the Run's oldest FIFO delivery (up to 50 messages) and **replays that
  same batch until you `--ack <delivery_id>`** — process everything in it before acking.
- Only these end a phase: `worker_done`, `escalation`, a provably dead dispatch
  (`worker-show` state failed/stopped), or a vanished terminal. No wall-clock kill.

## 7. Completion, release, retry

- Read the **first line** of the `worker_done` body: `PHASE-STATUS: complete` or
  `PHASE-STATUS: continuation-required`. A body with no status line is treated as
  complete. The `--outcome` flag is **not** the phase signal — a clean continuation pause
  reports `succeeded` (see §8b).
- A valid `worker_done` auto-completes both task and dispatch — never also
  `task-update --status completed`.
- `worker-release --dispatch <id>` on **every** terminal state (complete, continuation,
  failed, blocked) before moving on; it closes only that dispatch's terminal. Use
  `worker-retain` only if the user asks to keep a session open.
- Retry semantics: `--retry-of` **with** `--terminal` is only for the undelivered-prompt
  case (§5). A `failed` outcome retries as a **fresh dispatch** on the same task — new
  `worker-start` with placement and agent/model restated (retry does not inherit
  placement) and the failure summarized in a regenerated phase file. Orca circuit-breaks a
  task after 3 consecutive failures; run-plan's `max_consecutive_failures` (default 3)
  aligns with it.
- Answer a worker's `ask` with `reply --id <msg_id> --body "<answer>" --json`. Send
  mid-phase follow-ups with `send --to dispatch:<id> ...` — never by typing into its
  terminal.

## 8. Canonical worker-facing text

### 8a. First-contact preamble (opens every phase file)

> **Before anything else — before you read a single file** — tell the coordinator you have
> this prompt. Send a `heartbeat` exactly as the lifecycle preamble Orca injected into
> your terminal with your dispatch shows it, with `--phase starting` and the subject
> `session <n> started`. The
> preamble supplies your terminal handle, dispatch capability, task id and dispatch id;
> take all four from there. This one command is what tells the coordinator the dispatch
> landed rather than being swallowed at startup, and until it arrives the coordinator
> cannot tell you apart from a worker that never woke up.

### 8b. Completion paragraph (closes every phase file)

> When finished, send your completion via the Orca orchestration CLI: load the
> `orchestration` skill and send a `worker_done` message with your injected task id and
> dispatch id, `--outcome succeeded` (or `failed`), and the list of files you modified. Put
> in the body: what changed, the commit SHA, test results, and anything the next session
> must know. Make `PHASE-STATUS: complete` the first line of the body. Send it exactly
> once. If you are blocked on a decision only the coordinator can make, use the
> orchestration `ask` flow and wait for the reply — do not guess, and do not go idle.
>
> If instead you stopped at your context budget (see the continuation instructions above),
> send the same `worker_done` with `--outcome succeeded` — the session did its job by
> stopping cleanly — but make the subject `session <n> paused at context budget` and the
> **first line of the body** `PHASE-STATUS: continuation-required`, then name the
> continuation note by path and stop. Do not use `--outcome failed`: nothing failed, and a
> failure counts against the task's three-strike circuit breaker.
>
> Never signal completion by writing a sentinel file or by going idle — `worker_done` is
> the only completion signal the coordinator trusts.
