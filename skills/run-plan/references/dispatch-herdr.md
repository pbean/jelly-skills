# Dispatch backend: herdr

This file is run-plan's herdr dispatch backend, and its section numbers are the contract
SKILL.md cites (see SKILL.md "Dispatch backends").

It documents the loop **shape** and the pitfalls, for herdr 0.9.5 and later. The command
lines below were checked against herdr 0.9.5's group listings and its
`agent-automation.mdx`, `socket-api.mdx` and `cli-reference.mdx` docs on 2026-10-19. They
are **not live-validated**: the JSON shapes below come from those docs and the 0.9.5
source, not from an observed run. Flags rot across releases, so the listings you print in
§0 win wherever the two disagree. Commands are written `herdr <group> <verb> …`.

**What herdr lacks, compared with Orca.** herdr is a terminal multiplexer that recognizes
agents in its panes and tracks their lifecycle state (`idle | working | blocked | done |
unknown`). It has no message bus (no heartbeat, no completion message, no ask/reply), no
task DAG, no server-side run and so no run fencing, and no failed-start breaker. So the
report channel is **files in `<runs dir>`** plus the agent's **lifecycle state**: the
worker writes a result file (or an ask file) and ends its turn, `herdr agent wait`
settles, and the coordinator reads the file. `run-state.json` stands in for run fencing,
by convention (§1).

**Output and errors.** Every command prints JSON. Success is
`{"id": …, "result": {"type": "<snake_case>", …}}`. An error is
`{"id": …, "error": {"code": …, "message": …}}` on **stderr** with exit 1, and a
`timeout` is exit 1 too, so branch on `.error.code`, never on the exit code alone. Exit 2
means the CLI rejected the syntax: fix the command, don't retry it. An agent's state is
the `agent_status` field of the agent object (not `status` or `state`).

## 0. Resolve the binary, check the server

- `herdr` must be on PATH and `HERDR_ENV` must be `1`. Otherwise refuse the run with "run
  the coordinator inside a herdr pane": **never control herdr from outside it**. herdr
  also sets `HERDR_PANE_ID`, `HERDR_TAB_ID`, `HERDR_WORKSPACE_ID` and `HERDR_SOCKET_PATH`
  in every pane; §1 records `HERDR_PANE_ID`.
- **Never run bare `herdr`** (it launches the TUI), never `herdr server stop`, and never
  probe a mutating verb by leaving out its arguments: `tab create` and `workspace create`
  run with defaults.
- `herdr status`: the server must report `status: running` and `endpoint_compatible: yes`.
  Record the client and server `version` lines in `run-log.md`.
- Print the group listings once: `herdr agent`, `herdr tab` and `herdr pane`, each with
  **no verb**. They are the usage lines for every command below and win over this file.
  `herdr <group> <verb> --help` does not work; it prints the top-level help.
- `herdr integration status --outdated-only`. If the worker kind's integration is listed
  (outdated), or `herdr integration status` shows it `not installed`, warn the user once
  that state detection is weaker without it, and name
  `herdr integration install <integration>`. The integration name is the kind, except
  `agy`, whose integration is `antigravity-cli`. **Never install it yourself.**
- When run-config has no `agent_args`, warn the user once about permission posture:
  herdr launches the agent's own executable with no flags but the ones §3 builds, so an
  unattended worker parks on its first permission prompt (`blocked`) until a person
  answers it.

## 1. Run binding

There is no server-side run. At run start, mint the run id `rp<run8>`, where `<run8>` is 8
random lowercase hex digits (`python3 -c 'import secrets; print(secrets.token_hex(4))'`),
and write it to `run-state.json` (atomically) with the owner. Agent names are unique per
herdr server, not per run, so the id must make a clash with another live run negligible:

```json
"backend": "herdr",
"run_id": "rp3f9a2c71",
"owner": {"pane_id": "<$HERDR_PANE_ID>", "generation": 1}
```

`generation` is `coordinator_generation`. This is **fencing by convention**: re-read
`owner` before every dispatch and every release. If it names another pane, another
coordinator holds the run: stop dispatching and tell the user. Never take it back by
rewriting `owner`.

On resume, keep the recorded `run_id`. Rewrite `owner` to your own pane only after you
confirm with the user that the old owner is gone: `herdr pane get <old pane_id>` answers
an error, or the pane is not running run-plan. Coordinator succession (§9) rewrites
`owner` deliberately.

## 2. One leg record per attempt, minted at start

A phase runs as one or more **legs**: leg 1 is its first worker, and each continuation
(SKILL.md failure machine) is the next leg. herdr has no task object, so nothing is
created ahead of time and nothing chains the legs. The **leg record** in `run-state.json`
is:

- `task_id`: always `null`.
- `dispatch_id`: the agent name of the leg's **current attempt**,
  `rp<run8>-s<N>-l<g>-a<k>` (phase N, leg g, attempt k from 1). It always matches herdr's
  name rule `[a-z][a-z0-9_-]{0,31}`.
- `tab_id` and `pane_id`: the attempt's tab and its root pane (§3).
- `outcome`, as in SKILL.md Step 2.

A retry on the same leg (§7) replaces `dispatch_id`, `tab_id` and `pane_id` with the new
attempt's. Every attempt gets a fresh name, so a late file from an earlier attempt can
never pass for the current one. Record each id the moment its command returns.

The phase file for an attempt carries that attempt's paths (§8b): the **result file**
`<runs dir>/phase-N-l<g>-a<k>.result.md` and the **ask files**
`<runs dir>/phase-N-l<g>-a<k>.ask-<q>.md`. Assemble `phase-N.txt` for the attempt before
§3, since its paths name `k`.

Rediscovery after lost context: `herdr agent list` (`.result.agents[]`, each with `name`,
`tab_id`, `pane_id`, `agent_status`) and `herdr tab list`, matched by agent name and tab
label against the legs in `run-state.json`.

## 3. Starting a worker

Each attempt gets a new tab in `run-config.cwd`, not focused, in your own pane's
**current** workspace. Resolve it right before each `tab create`:

```
herdr pane current --current
herdr tab create --workspace <workspace_id> --cwd <run-config.cwd> --label "S<N> leg <g>: <phase title>" --no-focus
```

`<workspace_id>` is `.result.pane.workspace_id` from the first call. Without `--workspace`
herdr uses the *focused* workspace, so a user looking at another one would get the run's
tabs there. Don't use `$HERDR_WORKSPACE_ID`: a process keeps its launch-time value after
its pane is moved to another workspace, and that workspace may since have been closed.
`--current` still resolves after a move, because herdr keeps the old pane id as an alias.
Record
`.result.tab.tab_id` and `.result.root_pane.pane_id` at once. Then start the agent in that
pane:

```
herdr agent start <name> --kind <run-config.agent> --pane <pane_id> --timeout 120000 -- <args>
```

- `--kind` is run-config's `agent`; `herdr agent` lists the kinds. The pane must be at an
  idle shell prompt, which a fresh tab's root pane is. `--timeout` must be over 3000 and at
  most 300000 ms.
- `<args>` is the model and effort for the kind, then `agent_args` verbatim when
  run-config sets it. If `agent_args` already sets the model or effort, leave the table's
  flag out. Re-check the table against the agent's own `--help` in §0's pass:

  | kind | model | effort (only with a model) | checked |
  | --- | --- | --- | --- |
  | `claude` | `--model <m>` | `--effort <e>` | Claude Code 2.1.303 |
  | `codex` | `-m <m>` | `-c model_reasoning_effort=<e>` | codex-cli 0.163.0 |
  | any other | none | none | log that `model`/`effort` were ignored |

- Arguments after `--` reach the agent unchanged, and herdr adds no launch defaults of its
  own, so the posture `agent_args` sets is the whole of it.

**Reading the start receipt.**

- **Success** returns `type: "agent_started"` with `.result.agent` (the agent object) and
  `.result.argv` (the argv herdr launched). That is the start receipt. Compare `argv` with
  the args you asked for; if they differ, log it and tell the user. Don't re-dispatch over
  it. The receipt proves the agent is ready for input, not that a turn started: §5 proves
  that.
- **`agent_not_ready`**: startup hit a dialog (trust or permissions), so the agent is
  `blocked`. The name is still valid for `agent read` and `agent send-keys`. Tell the user
  what it shows (one `herdr agent read <name> --source visible`), then wait for them to
  clear it: `herdr agent wait <name> --until idle --until done --timeout 600000`, re-issued
  on `timeout`. Then go on to §4.
- **`agent_name_taken`**: a live agent already has this name. Check `herdr agent get
  <name>`: if its `tab_id` is this attempt's recorded tab, an earlier start of this attempt
  went through, so treat it as the lost-receipt case below. Any other tab means another
  run holds the name: never prompt or close that agent. Close this attempt's tab and mark
  the phase `[blocked]` with the clash.
- **A timeout or any other error** (`agent_pane_busy`, `unsupported_agent_kind`,
  `invalid_agent_argument`, …): read once (`herdr agent read <name> --source visible`;
  it errors if no agent came up), close the tab (`herdr tab close <tab_id>`), and count a
  failed attempt (§7). An error a fix to run-config would cure (a bad kind or argument) is
  for the user: mark the phase `[blocked]` with the error rather than spend attempts.
- **Lost receipt** (a transport error with no JSON). Never re-run `agent start` blind: a
  second agent in another tab puts two editors in one checkout. Check `herdr agent get
  <name>` first. If it answers the agent, carry on from that as the receipt; if it answers
  `agent_not_found`, check `herdr tab list` for the recorded tab, close it, and count a
  failed attempt.

A start whose outcome is unknown is the lost-receipt case: settle it by `agent get`
before anything else.

## 4. Delivering the prompt

Delivery is one line, sent by the coordinator after the start receipt:

```
herdr agent prompt <name> "Read <abs runs dir>/phase-N.txt and execute it exactly as written." --wait --until working --until blocked --timeout 60000
```

`phase-N.txt` must exist before this call. `agent prompt` writes the text with bracketed
paste and then a delayed Enter, as one submission. **Never send multi-line text** through
herdr, and never use `herdr pane send-text` for a prompt: the full wrapped prompt lives in
the phase file (assembled per plan-format.md §7 with this file's §8a and §8b paragraphs),
and the delivered line only points at it.

## 5. First contact — prove the prompt landed

The `agent prompt` call above is the proof. With `--wait`, it returns once herdr has seen
activity and then a state in `--until`:

- **Returns `agent_status: "working"`** (`type: "agent_prompted"`, `.result.agent`): the
  prompt landed and the turn started. Go to §6. No heartbeat is needed.
- **Returns `agent_status: "blocked"`**: a dialog came up as the turn started. Do one
  `herdr agent read <name> --source visible`, tell the user what it shows, and wait for
  them: `herdr agent wait <name> --until working --until idle --until done --timeout 600000`,
  re-issued on `timeout`. **Never re-deliver over a dialog.** Then go to §6.
- **`agent_blocked`**: the agent was already `blocked` when you prompted, and herdr sent
  nothing. Handle the dialog as above, then deliver (§4). This is still the first
  delivery.
- **`agent_prompt_stalled`** (no `working` or `blocked` within 5 s of submission) **or
  `timeout`**: neither proves the prompt was lost. A fast turn can also start and finish
  between observations. Check for the result file first (§6); if it is there, go to §7.
  Otherwise do one `herdr agent read <name> --source recent-unwrapped --lines 40`:
  - Work is visible → the prompt landed; go to §6.
  - The line sits unsubmitted in the composer → `herdr agent send-keys <name> enter` once,
    then §6.
  - The composer is empty → the prompt was swallowed. Re-deliver once, with the same
    `agent prompt` command.
- **Never a third delivery.** Still no proof after the one re-delivery (or the one Enter)
  → mark the phase `[blocked]` with what was observed (the error code and the read's last
  line), release (§7), stop.

## 6. Waiting

Rolling waits, re-issued in a loop until the phase settles:

```
herdr agent wait <name> --until idle --until done --until blocked --timeout 900000
```

It returns `type: "agent_info"` with `.result.agent`; read `agent_status`. A `timeout`
error is a checkpoint, not a failure: quiet is not dead. On a settled state, look at the
**files**, never at the state alone (`idle`, `done` and `unknown` don't prove the work is
done):

- **The result file** for the current attempt exists → completion report; go to §7.
- **Else an ask file** for the current attempt exists with no matching reply → an
  escalation (SKILL.md failure machine). To answer it, write the reply atomically to
  `<runs dir>/phase-N-l<g>-a<k>.reply-<q>.md` (same `<q>` as the ask), then, only while the
  agent is `idle` or `done`, send:
  `herdr agent prompt <name> "Read <abs reply path>; it answers your question. Continue the phase." --wait --until working --until blocked --timeout 60000`,
  and handle its return as §5 does.
- **Else `blocked`** → one `herdr agent read <name> --source visible`, tell the user what
  the dialog is, and keep waiting with `--until idle --until done`. A worker parked on a
  dialog is healthy, not failed; it waits until a person answers.
- **Else `idle` or `done` with no file** → the worker ended its turn without reporting. Do
  one `herdr agent read <name> --source recent-unwrapped --lines 40`:
  - A question in prose → treat it as an escalation, and answer it with a reply file as
    above.
  - Visible background work still running (a job the agent launched and is waiting on) →
    keep waiting; it isn't a stop.
  - Otherwise nudge once:
    `herdr agent prompt <name> "You ended your turn without writing <abs result path>. If the phase is finished, write it now as the end of your phase file says; otherwise continue." --wait --until working --until blocked --timeout 60000`.
  - A second report-less stop in the same attempt is a failed attempt (§7).
- **`unknown`** is never proof of anything. Keep waiting, and include `--until unknown`
  only when you want to look at it.
- **After three consecutive timeouts**, run `herdr agent get <name>`. Only
  `agent_not_found` or `agent_not_running` **and** a pane back at its shell prove the
  worker is dead: check with `herdr pane process-info --pane <pane_id>`, whose
  `.result.process_info.foreground_processes` then holds only the shell. A `herdr tab get
  <tab_id>` that errors (someone closed the tab) is proof too. Proven dead → a failed
  attempt (§7). Reset the timeout count after looking.
- **Absence authorizes nothing.** No other pane reads (`herdr pane read` buys repaint
  noise), no wall-clock kill. If `agent read --lines` answers `agent_not_idle`, read
  `--source visible` instead.

## 7. Completion, release, retry

- **The completion report** is the result file plus a settled state. It is valid only if
  its name carries the **current** attempt's suffix (`-l<g>-a<k>`). A result file from an
  earlier attempt is logged and ignored.
- Read its **first line**: `PHASE-STATUS: complete`,
  `PHASE-STATUS: continuation-required` or `PHASE-STATUS: failed`. The three-sentence
  summary follows (§8b). `failed` is the completion report with a failed outcome (SKILL.md
  failure machine): release, then retry on the same leg (below). A file with no status
  line counts as complete.
- **Release** = `herdr tab close <tab_id>`, on **every** settled attempt (complete,
  continuation, failed, blocked) before moving on, and only for tabs recorded in
  `run-state.json`. Closing the tab ends the agent, and herdr clears its name, so
  `herdr agent get <name>` then answers `agent_not_found`. If `tab close` answers
  `confirmation_required`, tell the user rather than force it. Keep a tab open only if the
  user asks.
- **A failed attempt retries on the same leg**: release the attempt's tab, summarize the
  failure into a regenerated `phase-N.txt` (with the next attempt's paths, §2), then start
  attempt `k+1` with a new tab and a new name, `rp<run8>-s<N>-l<g>-a<k+1>`, and run
  §3-§5 again. herdr has no failed-start breaker, so run-plan's `max_consecutive_failures`
  is the only bound, and it counts every failed attempt, starts included.
- **A continuation is a new leg, never a retry**: release, then start leg g+1 at attempt
  1 (`-l<g+1>-a1`), §3.
- **Escalations** are answered with a reply file and a one-line prompt (§6). There is no
  way to send a mid-phase follow-up while the worker is `working`: typing into a busy
  agent races its own output. Queue a follow-up for the next settled state, or leave it
  out.
- **Before ending the run** (Completed or Blocked; SKILL.md Exit states), sweep: for every
  leg's `tab_id` in `run-state.json`, if `herdr tab get <tab_id>` still finds it, close it.
  The run isn't over until none is left. **Never sweep on a pause on a question**: the
  asking worker's tab stays open, since the reply (§6) is delivered to that live agent.
  Leave any tab the user asked to keep open too.

## 8. Canonical worker-facing text

The runner substitutes `<runs dir>`, `N`, `<g>`, `<k>`, `<result path>` (the attempt's
result file, §2) and `<ask path>` (the attempt's ask file with `<q>` left in, for the
worker to number) into both paragraphs.

### 8a. First-contact preamble (opens every phase file)

> You were dispatched by run-plan through herdr. There is no orchestration CLI here: do not
> load Orca or herdr skills, and do not run `herdr` or control other panes or tabs. Your
> prompt has landed; the coordinator saw your turn start, so there is no heartbeat to send.
> You report only by writing files in `<runs dir>`, as the end of this file says.

### 8b. Completion paragraph (closes every phase file)

> When finished, write `<result path>` **atomically**: write a temp file in the same
> directory, then `mv` it onto that name. Make `PHASE-STATUS: complete` its **first line**,
> then a three-sentence summary: what changed (with the commit SHA), what you found (with
> test results), and what remains or what the next session must know. Then end your turn.
>
> If instead you stopped at your context budget (the context-budget paragraphs above, and
> context-budget `references/worker.md`), write the same file with the first line
> `PHASE-STATUS: continuation-required`, name the continuation note by path in its body,
> and end your turn. Nothing failed: the session did its job by stopping cleanly.
>
> If you cannot finish the phase for any other reason (a step that won't pass, a broken
> environment, work you had to abandon), write the same file with the first line
> `PHASE-STATUS: failed`, then say what you tried, what failed (with the error), and the
> state you left the checkout in. End your turn. The coordinator retries the phase in a
> fresh session, so never report `complete` for unfinished work.
>
> If you are blocked on a decision only the coordinator can make, write your question to
> `<ask path>` (atomically, with `<q>` = 1 for your first question, 2 for the next, and so
> on) and end your turn — do not guess. The answer arrives as a prompt naming a reply file;
> read it and continue.
>
> In this run, the result file, followed by the end of your turn, is the only completion
> signal the coordinator trusts. Never end a turn without writing one of these files
> unless you are waiting on an ask.

## 9. Coordinator succession

The backend half of `coordinator-handoff.md`: that file says when and why, this section
says which commands. Checked against herdr 0.9.5's group listings, `socket-api.mdx` and
`agent-automation.mdx` on 2026-10-19, and against `ctxcheck launch-command`'s runtime
list. **Not live-validated**, like §§3-6. herdr has no run to rebind and no fencing, so
the takeover is confirmed by `run-state.json` alone (§1's `owner` is the convention that
stands in for a fence).

**Am I inside a herdr pane?** (handoff step 1.) Yes when `HERDR_ENV` is `1`, `$HERDR_PANE_ID`
is non-empty, **and** this answers `type: "pane_info"`:

```
herdr pane current --current
```

Don't require `.result.pane.pane_id` to equal `$HERDR_PANE_ID`. A cross-workspace
`pane move` changes the live id, and the process keeps its launch-time `$HERDR_PANE_ID`,
which herdr retains as an alias (0.9.5 cli-reference). So `$HERDR_PANE_ID` is your
identity for the life of the process. That is why §1 records it, and why the successor
records its own. Also check that `run-state.json`'s `owner.pane_id` equals
`$HERDR_PANE_ID` (§1). An error, a missing variable or a mismatched owner means no: take
the handoff fallback. Never probe with a bare
`herdr pane current` (it answers for the focused pane, which can be another agent's).

**Settle the inbox** (handoff step 2). herdr has no inbox and no deliveries to
acknowledge, so there is nothing to settle and handoff step 4 has nothing to hold back.
Go straight to the note.

**Launch the successor** (handoff step 6). Open a new tab in your current workspace,
resolved right before as in §3, and `run-config.cwd`, not focused:

```
herdr pane current --current
herdr tab create --workspace <workspace_id> --cwd <run-config.cwd> --label "run-plan coordinator gen <G+1>" --no-focus
```

Record `.result.tab.tab_id` and `.result.root_pane.pane_id` at once, in `run-log.md` and
in the `handoffs[]` entry as `successor_tab` and `successor_pane`. Never record them as a
leg's `tab_id`: §7's end-of-run sweep closes leg tabs, and a later generation would close
the tab its own coordinator runs in. The successor's agent name is `<run_id>-coord-g<G+1>`
(for example `rp3f9a2c71-coord-g2`), which matches herdr's name rule.

Then start the agent from `ctxcheck launch-command --json`. Its `runtime` picks the path:

| `runtime` | herdr `--kind` |
| --- | --- |
| `claude`, `codex`, `opencode`, `pi`, `cursor`, `copilot` | the same name |
| `antigravity` | `agy` |
| anything else | none: use `pane run` below |

Each kind is in `herdr agent`'s kinds list (0.9.5). When the runtime has a kind **and**
`argv[0]` is the agent's own executable, start it readiness-aware, with `argv[1:]` as the
arguments. Judge `argv[0]` by its basename, with any directory and `.exe` stripped:
`/usr/bin/node` is still an interpreter. An interpreter is `node`, `nodejs`, `bun` or
`deno`, optionally followed by a version (`node22`), and its `argv` carries an
interpreter and a script path first, so it never takes this path:

```
herdr agent start <successor> --kind <kind> --pane <successor_pane> --timeout 120000 -- <argv[1:]>
```

- Pass `argv[1:]` as separate arguments, unchanged. They are your own launch posture
  (permissions, sandbox, model) minus resume arguments and the initial prompt, and herdr
  adds no launch defaults of its own. Never build them from the agent name.
- **Success** (`type: "agent_started"`) means the agent is ready for input. Compare
  `.result.argv` with `launch-command`'s `argv`; if the executable or the arguments differ
  (herdr runs the kind's canonical executable from PATH), log it and tell the user, then go
  on.
- **`agent_not_ready`**: a trust or permission dialog came up at startup. That is handoff
  step 8's dialog branch, with nothing sent yet.
- **A timeout or any other error**: the one diagnostic read below, then handoff step 8.
  On a lost receipt, settle it with `herdr agent get <successor>` before anything else,
  never a second `agent start`.

Otherwise (no kind, or an interpreter `argv`), type your own command into the pane and
detect the agent:

```
herdr pane run <successor_pane> "<shell_command>"
```

`<shell_command>` is `launch-command`'s `shell_command`, verbatim, as one argument. Then
poll for detection, bounded at 180 s in one shell call, and name it. The deadline is in
Python, not GNU `timeout`, which stock macOS lacks:

```
python3 - <successor_pane> <<'EOF'
import subprocess, sys, time
deadline = time.monotonic() + 180
while subprocess.run(["herdr", "agent", "get", sys.argv[1]], capture_output=True).returncode:
    if time.monotonic() > deadline:
        sys.exit(124)
    time.sleep(3)
EOF
herdr agent rename <successor_pane> <successor>
```

Exit 124 means herdr never detected an agent there: the one diagnostic
read, then handoff step 8. Once named, wait for its interface, since a line typed into a
booting agent can be lost:
`herdr agent wait <successor> --until idle --until blocked --timeout 120000`. `blocked` is
handoff step 8's dialog branch; a `timeout` error is the diagnostic read.

**Send one line** (handoff step 6 has the text; it has no newline in it):

```
herdr agent prompt <successor> "<one line>" --wait --until working --until blocked --timeout 60000
```

- **Returns `agent_status: "working"`**: the line was submitted and the turn started. Go
  to the takeover wait.
- **Returns `agent_status: "blocked"`, or `agent_blocked`**: a dialog. That is handoff step
  8's dialog branch: tell the user, and don't re-send.
- **`agent_prompt_stalled` or `timeout`**: neither proves the line was lost. Do the one
  diagnostic read now instead of waiting ten minutes first, and decide from it:
  - Work is visible → the line landed. Go to the takeover wait; if that runs out, the
    succession failed (handoff step 8's last branch, with no second read).
  - The line sits unsubmitted in the composer → `herdr agent send-keys <successor> enter`
    once, then the takeover wait as above.
  - Anything else → handoff step 8 (a dialog, an empty composer, or failure).

**Confirm the takeover** (handoff step 7). The successor's §1 rebind is a `run-state.json`
write: in the same guarded write (`coordinator-handoff.md`, "Guarded writes") as its
`takeover: {generation: G+1, at}` record, it rewrites `owner` to `{"pane_id": "<its $HERDR_PANE_ID>", "generation": <G+1>}`. A successor
whose first line names generation `takeover_pending` rewrites `owner` without §1's resume
confirmation: the predecessor's handoff is that confirmation. There is no fenced signal to
wait for, so poll the file, bounded at 600 s, in one shell call (a Python deadline, as
above):

```
python3 - "<runs dir>/run-state.json" "<G+1>" <<'EOF'
import json, sys, time
deadline = time.monotonic() + 600
while True:
    try:
        with open(sys.argv[1]) as f:
            if (json.load(f).get("takeover") or {}).get("generation") == int(sys.argv[2]):
                sys.exit(0)
    except (OSError, ValueError):
        pass
    if time.monotonic() > deadline:
        sys.exit(124)
    time.sleep(5)
EOF
```

- **Exit 0**: `takeover.generation == G+1`, so the takeover is confirmed. Re-read
  `run-state.json`: `owner.pane_id` should now name the successor's pane. From here §1's
  fencing by convention applies to you: `owner` names another pane, so you dispatch and
  release nothing.
- **Exit 124** (the 600 s ran out): re-read `run-state.json` once; still no takeover →
  handoff step 8.
- If your shell tool caps one call below ten minutes, run the same loop as several shorter
  calls that add up to 600 s.

**The one diagnostic read** (handoff step 8). Exactly once, for diagnosis only, and only
of the successor, never a worker (§6):

```
herdr agent read <successor> --source visible
```

If it answers `agent_not_found` (herdr never detected an agent there), read the pane
instead, which is still the one read: `herdr pane read <successor_pane> --source visible --lines 40`.
Decide from it per handoff step 8: a dialog → tell the user; an empty composer → the one
re-send (the same `agent prompt` above), then the takeover wait once more.

**If the succession failed**, leave the successor's tab open and name it to the user (its
label, `successor_tab` and `successor_pane`) rather than closing it yourself. With
`takeover_pending` cleared by your guarded write, a successor that wakes later fails its
guard and stands down without touching `owner`. Never close your own tab either: the user closes it once you end your turn.
