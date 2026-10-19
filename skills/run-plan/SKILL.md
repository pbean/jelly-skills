---
name: run-plan
description: >-
  Execute a phased plan file as an unattended coordinator: for each pending
  phase, dispatch a FRESH agent session (agent, model and effort from the
  plan's run config) via the configured dispatch backend (Orca orchestration or herdr), deliver
  the phase prompt, wait for completion, verify the plan file was updated, and
  advance — stopping only when the plan is done or a phase is blocked. Use
  when the user says "run the plan", "run this plan", "/run-plan
  <plan-file>", "execute the plan", or "dispatch the plan", or asks to drive a
  plan's phases as fresh sessions. Plans come from the session-plan skill.
argument-hint: <path-to-plan.md>
license: MIT
compatibility: >-
  Requires Orca orchestration or herdr: the Orca CLI with orchestration
  (orca-ide on Linux), or herdr with the coordinator running inside a herdr
  pane. The coordinator can be any agent with a shell; workers can be any agent
  id the backend supports (claude, codex, cursor, opencode, ...).
metadata:
  author: pbean
  version: "1.4.0"
---

# Run a plan

Drive a multi-phase plan to completion by giving every phase what it was designed for: a
fresh session with a full context window. You are the **coordinator**. You never implement,
never write plan prose, and read repo files only to verify a specific claim. Your context is
the run's scarcest resource — spend it on dispatch decisions, not on watching workers.

So you measure yourself, and when you go over budget you hand the run to a **successor
coordinator** and stop ("Context budget" below). The plan's status tags plus the runs
directory carry everything a successor needs.

## Context budget

You measure your own context with the sibling context-budget skill's `ctxcheck` (resolved
in Step 0; below, `ctxcheck` means `python3 <ctxcheck_path>`).

- **Arm at the start of the run**, once Step 0 has resolved the path (a successor arms in
  its takeover steps instead). On Claude Code, invoke the context-budget skill first: that
  registers its backstop hook. Then:

  ```bash
  python3 <ctxcheck_path> --arm --role coordinator --label "run-plan <plan basename>" --gate <coordinator_handoff_pct>
  ```

  Pass `--gate` only when run-config sets `coordinator_handoff_pct`; without it the gate
  is derived from your window. **Exit 3** means this runtime can't be measured: say "no
  context budget on this runtime" once and carry on without checks (no succession then).
  With `ctxcheck_path: null`, skip all of this.
- **Check at every boundary with no worker in flight**: after a phase's worker is
  released, before loop step 2 flips the next phase to `[running]`, and after a
  continuation leg is released, before the next leg starts. A plain
  `python3 <ctxcheck_path> ; echo $?` uses what you armed. Never check mid-phase or on a
  timer.
- **Exit 10 → `references/coordinator-handoff.md`.** It finishes the last phase rather than
  hand off for it, and otherwise hands off to a successor coordinator that the backend
  launches in a fresh terminal with your own launch command, then confirms the takeover
  and ends your turn.
- **Disarm on every exit** (`python3 <ctxcheck_path> --disarm`; see Exit states), so the
  backstop doesn't keep warning a session that no longer drives the run.

## Contracts to load first

1. `references/plan-format.md` — the plan contract: run-config block, `### Session N
   prompt` headings, status tags (`no tag` = pending, `[running]`, `[done]`, `[blocked]`),
   prompt extraction, outcome records, write ownership, normalization, and the canonical
   wrapper suffix.
2. `references/dispatch-<backend>.md` — the dispatch file for this run's backend ("Dispatch
   backends" below). Load it as soon as Step 1 has resolved `backend`, before
   normalization; a successor reads
   `backend` from `run-state.json`. Follow its §0 before any backend command: it resolves
   the backend's binary and its version-matched guide or usage. Never guess flags from
   memory. Below, "dispatch §N" means section N of that file.
3. `references/coordinator-handoff.md` — **load on demand only**: when your boundary check
   exits 10, or when Step 0 finds a takeover pending. It holds the predecessor's handoff
   and the successor's takeover.

Every dispatch file defines these terms concretely for its backend:

- **leg record**: what a `legs[]` entry's `task_id` and `dispatch_id` hold (dispatch §2).
- **start receipt**: what starting a worker returns, including whether it proves the
  agent's turn started (dispatch §3, §5).
- **completion report**: the worker's one signal that it is finished. The first line of
  its body is `PHASE-STATUS: …` (dispatch §7, §8b).
- **escalation**: a worker's question or blocker for you, and how you reply to it.
- **release**: closing a settled worker's session (dispatch §7).
- **retry on the same leg**: a fresh attempt at a failed leg without starting a new one
  (dispatch §7).

## Step 0 — Resolve the plan

Take the plan path from the argument; if none was given, ask. Expand `~`, absolutize, and
read the whole file. Everything downstream cites this absolute path.

Then fix the **runs directory** — `<runs dir>` everywhere below:

- `<plan file's directory>/runs/<plan-basename>/` by default (a plan at
  `~/.claude/plans/foo.md` runs from `~/.claude/plans/runs/foo/`);
- but if the plan file sits inside a git work tree, `~/.agents/plans/runs/<plan-basename>/`
  instead — run state never lands inside a repo.

Then resolve **`ctxcheck`**, which measures each worker's context. It ships with the
sibling `context-budget` skill, at `../context-budget/scripts/ctxcheck` relative to this
skill's directory. Absolutize that path once, record it as `ctxcheck_path` in
`run-state.json` (Step 2), and substitute it as `<ABS_CTXCHECK>` in every phase file
(plan-format.md §7). If the sibling skill is missing, warn the user **once** that phases
will run without context budgets, record `ctxcheck_path: null`, and carry on. Never refuse
a run over it.

**Then check for a pending takeover.** If `<runs dir>/run-state.json` exists and has
`takeover_pending` set, a previous coordinator handed this run off. Load
`references/coordinator-handoff.md` and follow "Taking over" there: it decides from your
prompt whether you are that generation. As the successor you skip Step 1's
confirmations, never re-normalize or re-ask the run-config, and skip the Resume rule that
a `[running]` tag means "confirm with the user"; you resume the loop where the handoff note
and the records say. Otherwise, arm yourself (Context budget) and continue with Step 1.

## Step 1 — Normalize

**Resolve `backend` first**: the run-config defaults that normalization synthesizes depend
on it (plan-format.md §1). When `run-state.json` already exists, its recorded `backend`
wins: never detect or ask again mid-run. A `run-state.json` with no `backend` key predates
backends (run-plan 1.3 and older): take the legacy value §1 names. Otherwise use the plan's
`backend` key, or, when it is absent, detect it per §1.

Then load `references/dispatch-<backend>.md` and run its §0 checks **before normalizing**:
§0 may refuse the run (wrong environment, server missing or too old), and a refused run
must leave the plan and `run-state.json` untouched.

Then run the normalization procedure in plan-format.md §6: detect the layout, translate
legacy status markers, confirm the phase mapping with the user when the layout is legacy or
any status is ambiguous, synthesize and confirm (once) any missing run-config keys per §1,
annotate in place (tags + run-config, including the resolved `backend`), and extract every
pending phase's prompt to `<runs dir>/phase-N.src.txt`.

A plan with **no prompt-bearing phases is refused** with the message in that procedure —
never invent phases from prose.

## Step 2 — Run config and state

Parse the run-config block per plan-format.md §1; Step 1 has already synthesized,
confirmed, and written any missing keys, `backend` among them. Record the resolved
`backend` in `run-state.json` (a legacy state file without the key gets it now); its
dispatch file is already loaded (Step 1). Create the runs directory
`<runs dir>` containing:

- `run-state.json` — `backend`, the backend run id (dispatch §1), `ctxcheck_path`
  (Step 0), and per phase: the consecutive failure count, the literal heading line
  captured at normalization (headings are re-found by these literals from here on), and
  `legs`: one entry per worker leg, `{leg, task_id, dispatch_id, outcome}`. These keys are
  the same for every backend; the dispatch file defines their values (the leg record).
  Leg 1 is the phase's first worker; each continuation adds the next leg. A failed attempt retried on the same leg updates that
  leg's `dispatch_id`.
- Coordinator succession (`references/coordinator-handoff.md`): `coordinator_generation`
  (1 for the coordinator the user started), `takeover_pending` (the generation being
  handed to, else null), `takeover` (`{generation, at}`, written by the successor right
  after it rebinds the run), and `handoffs[]` (one `{generation, at_pct, after_phase, at}` per handoff, plus
  `successor_terminal` once the successor's terminal exists and `outcome: failed` if the
  succession fails).
- `run-log.md` — append-only: dispatches, retries, backfills, normalization summary.

**Every `run-state.json` write is atomic**: write a temp file in the same directory, then
rename it over the old one. A coordinator that dies mid-write must never leave a torn file
for its successor. Record each id the moment the backend returns it.

Run state lives here and in the plan's tags — never inside the target repo.

## Step 3 — The loop

For each phase N, from the first non-`[done]` phase to M, strictly sequentially:

1. **Re-read the plan.** Boundary-time edits by the user are honored (prompts are
   re-extracted at dispatch); edits to a phase already `[running]` are not — say so if you
   notice one.
2. **Set the heading tag to `[running]`.**
3. **Assemble `phase-N.txt`** in the runs directory, in this order:
   (a) the backend first-contact preamble (dispatch §8a);
   (b) orientation: *"You are executing Session N of M of the plan at `<abs path>`. Read
   the plan first — earlier sessions may have appended outcome notes addressed to you.
   Then execute the prompt below exactly as written."*;
   (c) the raw prompt from `phase-N.src.txt`, verbatim;
   (d) the canonical wrapper suffix (plan-format.md §7) with its placeholders substituted:
   this phase's literal heading line, the plan path, the runs dir, the leg number,
   `ctxcheck_path` and the `worker_handoff_pct` gate;
   (e) the backend completion paragraph (dispatch §8b).
   The backend paragraphs may carry placeholders of their own (a per-attempt result or
   ask file path, say); dispatch §8 defines them, and you substitute them here too.
   **The wrapper exists only in this file, never in the plan's fences** — the plan's
   prompts must stay clean enough to hand-paste.
4. **Create the leg record** per dispatch §2, following the previous leg (or the previous
   phase's last one), and record it in the phase's `legs`. The backend delivers the line
   `Read <abs phase file> and execute it exactly as written.` to the worker (dispatch §2,
   §4), which is why the phase file must exist before the leg record does.
5. **Start the worker** per dispatch §§1, 3 and 4, read its start receipt, and
   make **first contact** per §5. When the start receipt proves the agent's turn started
   (dispatch §5), the prompt landed and there is nothing to wait for. Otherwise: wait for
   the worker's first-contact signal, at most one look, at most one re-delivery, never a
   third.
6. **Wait** per dispatch §6 — rolling long waits; quiet is not dead; no pane reads;
   after three empty waits, enumerate and act only on positive evidence.
7. **On a completion report**, read the first line of its body and branch per the failure
   machine below.
8. **On `PHASE-STATUS: complete`**: verify the plan — heading `[done]`, outcome record
   present. Backfill per plan-format.md §5 if the worker forgot (log the warning). Release
   the worker, reset the phase's failure counter, log, then run the boundary check
   (Context budget) and continue to phase N+1.

## Failure machine

Per-phase states: `pending → running → done | blocked`. A `[blocked]` phase always carries
a `> **Blocked (date):** ...` blockquote naming the cause, dispatch id, and leg, and
the run stops there — later phases stay pending.

- **First-contact silence** → applies only when the start receipt did **not** prove the
  turn started (dispatch §5). Still silent after the one permitted re-delivery →
  `[blocked]` recording exactly what was observed (the start receipt's proof the turn
  started, dispatch §5, and its stage; the one diagnostic read's line). Stop. A
  permission or trust dialog is not silence: tell the
  user and keep waiting.
- **A start whose outcome is unknown** (dispatch §3) → inspect,
  settle the attempt explicitly as that section says, then treat it as a failed attempt
  (next bullet). Never re-dispatch over an unsettled attempt.
- **A failed attempt** (a completion report with a failed outcome, a failed start, or a
  proven-dead dispatch) → summarize the failure into a regenerated phase file and
  **retry on the same leg** (dispatch §7), placement and agent/model/effort restated.
  Count consecutive failures per phase; at `max_consecutive_failures` (default 3; a
  backend's own breaker, where it has one, counts only failed starts, so this counter is
  what bounds failures a worker reports)
  → `[blocked]` with all attempts summarized. Stop. The counter resets on any success.
- **`PHASE-STATUS: continuation-required`** → the phase stays `[running]`. Release the
  worker, run the boundary check (Context budget), and start leg g+1 as a **new leg
  record** following leg g's (dispatch §2) — a completed leg is never retried on the same
  leg. Its
  phase file gets one extra orientation line:
  *"This is leg g+1 of this phase — a previous session did part of it and stopped
  cleanly. Read `<runs dir>/phase-N-continuation.md` first; it supersedes this prompt
  wherever they disagree. Its edits are uncommitted in the worktree; reconcile, do not
  re-apply."* At `continuation_cap` legs total (default 3) → `[blocked]` with *"this phase
  repeatedly exceeds one session — split it in the plan."* Stop.
- **Escalation, or a question the plan doesn't answer** → if the answer is literally in the
  plan, reply via the backend and keep waiting. Otherwise surface the question to the user
  in this session and **end your turn** — never park it behind a backend gate (resolving a
  gate emits no message, so the wait would never wake). The phase stays `[running]`, the
  worker waits on its ask; when the user answers, relay the reply and resume waiting. If
  the user aborts instead, tell the worker to stop safely, release it, mark `[blocked]`.
- **Mid-phase quiet is not an event.** Only a completion report, an escalation, or a
  provably dead dispatch ends a phase (a dead dispatch counts as a failed attempt above).
  Missing evidence is not proof of death (dispatch §6). There is no wall-clock kill —
  killing a healthy slow worker is the worse failure, and the user can always interrupt
  this session.

## Exit states

Every exit leaves the plan self-describing. Every exit you take yourself also disarms
your context budget (`python3 <ctxcheck_path> --disarm`), except a pause on a question,
where you stay armed for when the run continues:

| Exit | Plan file reads | Also |
| --- | --- | --- |
| Completed | every phase `[done]` + outcome records | workers released; disarmed; summary printed |
| Blocked | failing phase `[blocked]` + cause blockquote; later phases pending | workers released; disarmed; resume instructions printed |
| Paused on a question | phase `[running]`; plan untouched | worker alive, waiting on the reply; still armed |
| Handed off | next phase pending (or a phase `[running]` mid-continuation) | disarmed before the successor launched; takeover confirmed; turn ended (`references/coordinator-handoff.md`) |
| Coordinator death | current phase `[running]` (stale) | recovered by the resume path below |

## Resume

Re-running this skill on a partly-run plan starts at the first non-`[done]` phase, reusing
`run-state.json` (re-bind the backend run per dispatch §1) and its recorded
`ctxcheck_path` (re-resolve it per Step 0 if that file is gone). A pending takeover in
`run-state.json` is not this path: Step 0 routes it to the successor branch. Otherwise, a
`[running]` tag found at start means a prior coordinator died mid-phase: confirm with the
user, then read that phase's `legs`. The last leg tells you where it stopped. Check its leg record
with the backend before acting: a worker that is still live is waited on, not re-dispatched.
Inspect `git log` / `git status` on the run-config branch to see what actually landed.

- If `phase-N-continuation.md` exists and the last leg reported
  `continuation-required`, start the next leg as a continuation.
- Otherwise, re-dispatch the last leg as a failed attempt (retry on the same leg,
  dispatch §7) with
  one extra wrapper line: *"A prior attempt at this phase may have partially completed —
  the plan's prompt already tells you to read the branch diff; reconcile, do not
  duplicate."* If that leg already completed, create a new leg instead.

A `[blocked]` tag is only cleared back to pending by the user (or by you at their explicit
instruction) — fix the cause first.

## End-of-run summary

Report: phases run this session with their commit SHAs (from outcome records), the branch,
retries and continuations used, any backfills, the blocked phase and cause if the run
stopped, the plan's absolute path, and the user's next action — review/PR/merge when
complete, or "fix X, clear the `[blocked]` tag, then run run-plan on `<path>` again".

## Dispatch backends

Each backend has one dispatch file in `references/`, and only that file names the
backend's commands:

| `backend` | File | Requires |
| --- | --- | --- |
| `orca` | `references/dispatch-orca.md` | Orca orchestration: the Orca CLI (`orca-ide` on Linux) |
| `herdr` | `references/dispatch-herdr.md` | herdr ≥ 0.9.5, with the coordinator running inside a herdr pane (`HERDR_ENV=1`) |

**The section contract.** Every dispatch file has the headings `## 0.` through `## 9.`,
with the same titles as `dispatch-orca.md` (retitled only where a backend lacks the
concept; the number never changes), plus `### 8a.` (the first-contact preamble) and
`### 8b.` (the completion paragraph). This file and `references/coordinator-handoff.md`
cite those sections by number only, and each dispatch file defines the vocabulary in
"Contracts to load first". To add a backend, write `references/dispatch-<name>.md` to
that shape, add its row here and its value to plan-format.md §1's `backend` key; nothing
else here changes.
