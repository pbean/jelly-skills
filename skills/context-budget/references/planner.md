# Planner: save and stop

Planning is the most context-hungry job in the flow. Exploration reads source,
issues and docs, and it fills a window fast. A planner that runs over doesn't
stop. It degrades: it forgets anchors it already found, re-asks questions the
user already settled, and writes session prompts on facts it can no longer
vouch for. A plan written that way looks finished, and every phase built on
it inherits the mistakes.

So a planner over budget **saves its work and stops**. It does not launch a
successor. Planning is a conversation with the user, and only the user can
open the next session and carry it on.

## Arming and checking

The session-plan skill arms the planner as its first action:

```bash
python3 "<context-budget dir>/scripts/ctxcheck" --arm --role planner --label "planning <topic>"
```

Then check with a plain `ctxcheck` at three boundaries:

- after exploration;
- after the design settles;
- before writing the session prompts.

Exit 10, or a backstop warning, means save and stop at the next **section
boundary**: finish the section or answer you are writing, but don't start
exploring a new area or begin a new section of the plan. Exit 3 means this
runtime can't be measured: say "no context budget on this runtime" once and
carry on.

## Save and stop

1. **Write the draft plan to its plan file** (the file session-plan names,
   at its absolute path). Write whatever is settled, in the plan template's
   section structure. Leave unfinished sections as headings with a one-line
   "not yet written" marker, so the next session can see the gaps. Don't
   fill gaps with guesses.
2. **Write the planning continuation** for a session that has none of this
   conversation:
   - **Explored:** what you read, with exact anchors (`path/to/file.py:123`,
     issue and PR numbers, doc URLs).
   - **Settled decisions:** each one with the date the user settled it and
     what they chose. The next session must not ask again.
   - **Open questions:** what still needs the user, and the options you
     would offer.
   - **Unverified claims:** anything in the draft taken from memory or a
     single source that still needs checking against code or current docs.
   - **Next steps:** the concrete order of work left, from the next area to
     explore through to writing the session prompts.

   Where it goes:
   - **Normally**, in its own file next to the plan:
     `<plan dir>/<plan basename>.planning-continuation.md` (for
     `~/.claude/plans/foo.md`, that is
     `~/.claude/plans/foo.planning-continuation.md`).
   - **Under Claude Code plan mode**, where only the plan file is editable,
     as a final `## Planning continuation` section of the draft plan
     instead. Don't try to leave plan mode to write the separate file.
3. **Disarm:** `ctxcheck --disarm`.
4. **Tell the user**, in one short message: that planning stopped at the
   context budget, where the draft and the continuation are, and exactly
   one line to paste into a fresh session:

   ```
   Use the session-plan skill to continue planning from <abs path>.
   ```

   `<abs path>` is the continuation file, or the plan file when the
   continuation is a section inside it. Always give the absolute path.

Then stop. Don't exit plan mode, don't ask for approval of a partial plan,
and don't "just finish" a section after the message.

## Resuming

A session given a planning continuation (a separate file, or a plan whose
last section is `## Planning continuation`):

1. Reads the continuation and the draft plan first, before exploring
   anything.
2. Re-arms as a planner (and, on Claude Code, invokes the context-budget
   skill first so its backstop registers).
3. Treats the settled decisions as settled. It never re-asks them, and it
   asks only the open questions.
4. Re-checks the unverified claims before building on them, then continues
   from the next steps.
5. Writes the finished plan to the same plan file, without the
   `## Planning continuation` section. A separate continuation file is
   deleted after approval (plan mode can't delete it), so a later session
   doesn't mistake it for live state.

A resumed planner can run over again. The same procedure applies: append to
the continuation rather than starting it over, so earlier findings survive.
