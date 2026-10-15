---
name: run-plan
description: >-
  Execute a phased plan file as an unattended coordinator: for each pending
  phase, dispatch a FRESH agent session (agent and model from the plan's run
  config) via the configured dispatch backend (Orca orchestration), deliver
  the phase prompt, wait for completion, verify the plan file was updated, and
  advance — stopping only when the plan is done or a phase is blocked. Use
  when the user says "run the plan", "run this plan", "/run-plan
  <plan-file>", "execute the plan", or "dispatch the plan", or asks to drive a
  plan's phases as fresh sessions. Plans come from the session-plan skill.
argument-hint: <path-to-plan.md>
license: MIT
compatibility: >-
  Requires the Orca CLI with orchestration (orca-ide on Linux). The
  coordinator can be any agent with a shell; workers can be any Orca agent id
  (claude, codex, cursor, opencode, ...).
metadata:
  author: pbean
  version: "1.0.0"
---

# Run a plan

Drive a multi-phase plan to completion by giving every phase what it was designed for: a
fresh session with a full context window. You are the **coordinator**. You never implement,
never write plan prose, and read repo files only to verify a specific claim. Your context is
the run's scarcest resource — spend it on dispatch decisions, not on watching workers.

If your own context degrades mid-run, stop cleanly at a phase boundary, summarize where the
run stands, and tell the user to re-run this skill on the plan path in a fresh session
(Claude Code: `/run-plan <plan path>`).
**Resume is the handoff mechanism** — the plan's status tags plus the runs directory carry
everything a successor needs.

## Contracts to load first

1. `references/plan-format.md` — the plan contract: run-config block, `### Session N
   prompt` headings, status tags (`no tag` = pending, `[running]`, `[done]`, `[blocked]`),
   prompt extraction, outcome records, write ownership, normalization, and the canonical
   wrapper suffix.
2. `references/dispatch-orca.md` — the dispatch backend. Follow its §0: resolve the Orca
   binary and load the version-matched orchestration guide before any orchestration
   command. Never guess flags from memory.

## Step 0 — Resolve the plan

Take the plan path from the argument; if none was given, ask. Expand `~`, absolutize, and
read the whole file. Everything downstream cites this absolute path.

Then fix the **runs directory** — `<runs dir>` everywhere below:

- `<plan file's directory>/runs/<plan-basename>/` by default (a plan at
  `~/.claude/plans/foo.md` runs from `~/.claude/plans/runs/foo/`);
- but if the plan file sits inside a git work tree, `~/.agents/plans/runs/<plan-basename>/`
  instead — run state never lands inside a repo.

## Step 1 — Normalize

Run the normalization procedure in plan-format.md §6: detect the layout, translate legacy
status markers, confirm the phase mapping with the user when the layout is legacy or any
status is ambiguous, annotate in place (tags + run-config), and extract every pending
phase's prompt to `<runs dir>/phase-N.src.txt`.

A plan with **no prompt-bearing phases is refused** with the message in that procedure —
never invent phases from prose.

## Step 2 — Run config and state

Parse (or synthesize and confirm — once) the run-config block per plan-format.md §1. Create
the runs directory `<runs dir>` containing:

- `run-state.json` — backend run id; per phase: task id, last dispatch id, consecutive
  failure count, continuation generation, and the literal heading line captured at
  normalization (headings are re-found by these literals from here on).
- `run-log.md` — append-only: dispatches, retries, backfills, normalization summary.

Run state lives here and in the plan's tags — never inside the target repo.

## Step 3 — The loop

For each phase N, from the first non-`[done]` phase to M, strictly sequentially:

1. **Re-read the plan.** Boundary-time edits by the user are honored (prompts are
   re-extracted at dispatch); edits to a phase already `[running]` are not — say so if you
   notice one.
2. **Set the heading tag to `[running]`.**
3. **Assemble `phase-N.txt`** in the runs directory, in this order:
   (a) the backend first-contact preamble (dispatch-orca.md §8a);
   (b) orientation: *"You are executing Session N of M of the plan at `<abs path>`. Read
   the plan first — earlier sessions may have appended outcome notes addressed to you.
   Then execute the prompt below exactly as written."*;
   (c) the raw prompt from `phase-N.src.txt`, verbatim;
   (d) the canonical wrapper suffix (plan-format.md §7) with this phase's literal heading
   line, plan path, and continuation-note path substituted;
   (e) the backend completion paragraph (dispatch-orca.md §8b).
   **The wrapper exists only in this file, never in the plan's fences** — the plan's
   prompts must stay clean enough to hand-paste.
4. **Dispatch** a fresh worker per dispatch-orca.md §§1–4. The delivery line
   `Read <abs phase file> and execute it exactly as written.` is the **task's spec**, set
   at task-create and injected by `worker-start` itself — there is no separate
   prompt-sending step, which is why the phase file must exist before dispatch.
5. **First contact** per dispatch-orca.md §5 — heartbeat wait, at most one look, at most
   one re-delivery, never a third.
6. **Wait** per dispatch-orca.md §6 — rolling long waits; quiet is not dead; no pane reads.
7. **On `worker_done`**, read the first line of the body and branch per the failure
   machine below.
8. **On `PHASE-STATUS: complete`**: verify the plan — heading `[done]`, outcome record
   present. Backfill per plan-format.md §5 if the worker forgot (log the warning). Release
   the worker, reset the phase's failure counter, log, and continue to phase N+1.

## Failure machine

Per-phase states: `pending → running → done | blocked`. A `[blocked]` phase always carries
a `> **Blocked (date):** ...` blockquote naming the cause, dispatch id, and generation, and
the run stops there — later phases stay pending.

- **First-contact silence** after the one permitted re-delivery → `[blocked]` recording
  exactly what was observed (receipt stage, the one `worker-read` line). Stop.
- **`worker_done` with a failed outcome** → summarize the failure into a regenerated phase
  file and retry as a fresh dispatch (placement and agent/model restated). Count
  consecutive failures per phase; at `max_consecutive_failures` (default 3, aligned with
  the backend's own circuit breaker) → `[blocked]` with all attempts summarized. Stop. The
  counter resets on any success.
- **`PHASE-STATUS: continuation-required`** → the phase stays `[running]`. Release the
  worker, increment the generation, and dispatch generation g+1 on the same phase with one
  extra orientation line: *"This is a continuation — a previous session did part of this
  phase and stopped cleanly. Read `<runs dir>/phase-N-continuation.md` first; its edits are
  uncommitted in the worktree; reconcile, do not re-apply."* At `continuation_cap` workers
  total (default 3) → `[blocked]` with *"this phase repeatedly exceeds one session — split
  it in the plan."* Stop.
- **Escalation, or a question the plan doesn't answer** → if the answer is literally in the
  plan, reply via the backend and keep waiting. Otherwise surface the question to the user
  in this session and **end your turn** — never park it behind a backend gate (resolving a
  gate emits no message, so the wait would never wake). The phase stays `[running]`, the
  worker waits on its ask; when the user answers, relay the reply and resume waiting. If
  the user aborts instead, tell the worker to stop safely, release it, mark `[blocked]`.
- **Mid-phase quiet is not an event.** Only `worker_done`, an escalation, a provably dead
  dispatch, or a vanished terminal ends a phase (a dead dispatch or vanished terminal
  counts as a failed attempt above). There is no wall-clock kill — killing a healthy slow
  worker is the worse failure, and the user can always interrupt this session.

## Exit states

Every exit leaves the plan self-describing:

| Exit | Plan file reads | Also |
| --- | --- | --- |
| Completed | every phase `[done]` + outcome records | workers released; summary printed |
| Blocked | failing phase `[blocked]` + cause blockquote; later phases pending | workers released; resume instructions printed |
| Paused on a question | phase `[running]`; plan untouched | worker alive, waiting on the reply |
| Coordinator death | current phase `[running]` (stale) | recovered by the resume path below |

## Resume

Re-running this skill on a partly-run plan starts at the first non-`[done]` phase, reusing
`run-state.json` (re-bind the backend run per dispatch-orca.md §1). A `[running]` tag found
at start means a prior coordinator died mid-phase: confirm with the user, inspect
`git log` / `git status` on the run-config branch to see what actually landed, then
re-dispatch that phase with one extra wrapper line: *"A prior attempt at this phase may
have partially completed — the plan's prompt already tells you to read the branch diff;
reconcile, do not duplicate."* If `phase-N-continuation.md` exists, dispatch it as a
continuation instead. A `[blocked]` tag is only cleared back to pending by the user (or by
you at their explicit instruction) — fix the cause first.

## End-of-run summary

Report: phases run this session with their commit SHAs (from outcome records), the branch,
retries and continuations used, any backfills, the blocked phase and cause if the run
stopped, the plan's absolute path, and the user's next action — review/PR/merge when
complete, or "fix X, clear the `[blocked]` tag, then run run-plan on `<path>` again".

## Swapping the dispatch backend

`references/dispatch-orca.md` is the only file that names the backend. To run phases over
herdr, plain tmux, or anything else: write `references/dispatch-<name>.md` with the same
section shape (first-contact preamble, completion paragraph, dispatch/wait/retry
procedure), point the "Contracts to load first" list at it, and nothing else here changes.
