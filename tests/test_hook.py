"""ctxcheck --hook: silent unless an armed session is over its gate; the Claude,
Codex, Cursor, Copilot and Antigravity output shapes; re-nagging; never
failing loudly.

The Cursor, Copilot and Antigravity payloads are synthetic, shaped after the
documented contracts cited in ctxlib/hook.py (none is live-verified)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import helpers

LABEL = "session 4 of /abs/plan.md"
OTHER_CODEX_ID = "019a0000-0000-7000-8000-0000000000ff"


class _Hook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bare_env = helpers.clean_env(self.tmp)
        self.env = dict(self.bare_env, CLAUDE_CODE_SESSION_ID=helpers.CLAUDE_SESSION)
        self.transcript = helpers.install_claude_transcript(self.env["HOME"])
        self.markers = Path(self.env["CTXCHECK_HOME"]) / "markers"

    def arm(self, role: str, *extra: str, env: dict | None = None):
        r = helpers.run_ctxcheck(["--arm", "--role", role, "--label", LABEL, *extra],
                                 env or self.env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def payload(self, **over) -> str:
        p = {"session_id": helpers.CLAUDE_SESSION, "transcript_path": str(self.transcript),
             "cwd": "/synthetic/project", "hook_event_name": "PostToolUse",
             "tool_name": "Bash", "tool_input": {"command": "echo hi"},
             "tool_response": {"stdout": "hi"}, "tool_use_id": "toolu_synthetic"}
        p.update(over)
        return json.dumps(p)

    def hook(self, stdin: str, *args: str, env: dict | None = None):
        # The hook gets the runtime's env, but must not depend on it: run with
        # the bare env so only the payload names the session.
        r = helpers.run_ctxcheck(["--hook", *args], env or self.bare_env, stdin=stdin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def assert_silent(self, r):
        self.assertEqual(r.stdout, "")

    def context(self, r) -> str:
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"hookSpecificOutput"})
        hso = out["hookSpecificOutput"]
        self.assertEqual(set(hso), {"hookEventName", "additionalContext"})
        self.assertEqual(hso["hookEventName"], "PostToolUse")
        return hso["additionalContext"]

    def marker(self, session: str = helpers.CLAUDE_SESSION) -> Path:
        return self.markers / f"{session}.json"


class SilenceTest(_Hook):
    def test_unarmed_session_is_silent(self):
        self.assert_silent(self.hook(self.payload(), "--runtime", "claude"))

    def test_armed_under_gate_is_silent(self):
        self.arm("worker")  # 4.3% against 28%
        self.assert_silent(self.hook(self.payload(), "--runtime", "claude"))

    def test_bad_input_exits_0_silently(self):
        self.arm("worker", "--gate", "1")
        cases = {
            "garbage": ("not json {", ()),
            "empty": ("", ()),
            "array": ("[1, 2]", ()),
            "unknown runtime": (self.payload(), ("--runtime", "no-such-runtime")),
            "no session": (json.dumps({"hook_event_name": "PostToolUse"}), ()),
        }
        for name, (stdin, args) in cases.items():
            with self.subTest(name):
                self.assert_silent(self.hook(stdin, *args))

    def test_missing_runtime_value_still_exits_0(self):
        # A dangling --runtime is treated as no --runtime: inferred from the
        # payload, never an argparse error.
        self.arm("worker", "--gate", "1")
        self.hook(self.payload(), "--runtime")
        self.assert_silent(self.hook("not json", "--runtime"))

    def test_corrupt_marker_is_silent(self):
        self.markers.mkdir(parents=True)
        self.marker().write_text("{not json")
        self.assert_silent(self.hook(self.payload(), "--runtime", "claude"))


class ClaudeMessageTest(_Hook):
    def test_worker_message(self):
        self.arm("worker", "--gate", "1")
        msg = self.context(self.hook(self.payload(), "--runtime", "claude"))
        self.assertIn("CONTEXT BUDGET EXCEEDED", msg)
        self.assertIn(LABEL, msg)
        self.assertIn("43,000/1,000,000", msg)
        self.assertIn("continuation note your phase file names", msg)
        self.assertIn("PHASE-STATUS: continuation-required", msg)

    def test_coordinator_message(self):
        self.arm("coordinator", "--gate", "1")
        msg = self.context(self.hook(self.payload(), "--runtime", "claude"))
        self.assertIn(LABEL, msg)
        self.assertIn("references/coordinator-handoff.md", msg)
        self.assertNotIn("PHASE-STATUS", msg)

    def test_planner_message(self):
        self.arm("planner", "--gate", "1")
        msg = self.context(self.hook(self.payload(), "--runtime", "claude"))
        self.assertIn(LABEL, msg)
        self.assertIn("references/planner.md", msg)
        self.assertNotIn("PHASE-STATUS", msg)

    def test_runtime_inferred_from_claude_payload(self):
        self.arm("worker", "--gate", "1")
        self.context(self.hook(self.payload()))

    def test_payload_transcript_path_is_preferred(self):
        self.arm("worker")  # gate 28%; the home transcript is at 4.3%
        elsewhere = self.tmp / "elsewhere" / f"{helpers.CLAUDE_SESSION}.jsonl"
        elsewhere.parent.mkdir()
        elsewhere.write_text(json.dumps({
            "type": "assistant", "sessionId": helpers.CLAUDE_SESSION, "isSidechain": False,
            "message": {"role": "assistant", "model": "claude-opus-5-5", "usage": {
                "input_tokens": 500_000, "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0, "output_tokens": 1}}}) + "\n")
        msg = self.context(self.hook(self.payload(transcript_path=str(elsewhere)),
                                     "--runtime", "claude"))
        self.assertIn("500,000/1,000,000", msg)

    def test_session_lookup_when_no_transcript_path(self):
        self.arm("worker", "--gate", "1")
        p = json.loads(self.payload())
        del p["transcript_path"]
        msg = self.context(self.hook(json.dumps(p), "--runtime", "claude"))
        self.assertIn("43,000/1,000,000", msg)


class RenagTest(_Hook):
    def test_warns_once_then_again_after_5_points(self):
        self.arm("worker", "--gate", "1")
        self.context(self.hook(self.payload(), "--runtime", "claude"))
        state = json.loads(self.marker().read_text())
        self.assertEqual(state["warnedAtPct"], 4.3)
        self.assert_silent(self.hook(self.payload(), "--runtime", "claude"))

        state["warnedAtPct"] = 0  # 4.3 < 0 + 5: still within the renag band
        self.marker().write_text(json.dumps(state))
        self.assert_silent(self.hook(self.payload(), "--runtime", "claude"))

        state["warnedAtPct"] = -1  # 4.3 >= -1 + 5: climbed 5 points since
        self.marker().write_text(json.dumps(state))
        self.context(self.hook(self.payload(), "--runtime", "claude"))
        self.assertEqual(json.loads(self.marker().read_text())["warnedAtPct"], 4.3)


class CodexTest(_Hook):
    def setUp(self):
        super().setUp()
        self.codex_env = dict(self.bare_env, CODEX_THREAD_ID=helpers.CODEX_THREAD)
        self.rollout = helpers.install_codex_rollout(self.codex_env["CODEX_HOME"])

    def codex_payload(self, session: str = helpers.CODEX_THREAD) -> str:
        return json.dumps({
            "session_id": session, "transcript_path": str(self.rollout),
            "cwd": "/synthetic/project", "hook_event_name": "PostToolUse",
            "model": "gpt-synthetic-1", "permission_mode": "default",
            "turn_id": "turn-synthetic", "tool_name": "Bash",
            "tool_use_id": "call_synthetic", "tool_input": {"command": "ls"},
            "tool_response": "ok"})

    def test_codex_shape_explicit_and_inferred(self):
        self.arm("worker", "--gate", "1", env=self.codex_env)
        for args in (("--runtime", "codex"), ()):
            with self.subTest(args=args):
                self.marker(helpers.CODEX_THREAD).write_text(json.dumps(dict(
                    json.loads(self.marker(helpers.CODEX_THREAD).read_text()),
                    warnedAtPct=None)))
                msg = self.context(self.hook(self.codex_payload(), *args))
                self.assertIn("31,200/258,400", msg)
                self.assertIn("PHASE-STATUS: continuation-required", msg)

    def test_codex_under_gate_is_silent(self):
        self.arm("worker", env=self.codex_env)  # 12.1% against 75%
        self.assert_silent(self.hook(self.codex_payload(), "--runtime", "codex"))

    def test_marker_found_via_rollout_filename(self):
        self.arm("coordinator", "--gate", "1", env=self.codex_env)
        msg = self.context(self.hook(self.codex_payload(session=OTHER_CODEX_ID),
                                     "--runtime", "codex"))
        self.assertIn("references/coordinator-handoff.md", msg)


SL_SESSION = "0d000000-0000-4000-8000-00000000b0b0"


class _StatuslineHook(_Hook):
    """An armed status-line session: a fresh sidecar written by the tee, then
    --arm from inside the 'session' (its env var, or Cursor's cwd)."""
    RUNTIME = ""
    ARM_ENV: dict = {}

    def setUp(self):
        super().setUp()
        self.work = self.tmp / "work"
        self.work.mkdir()
        sidecar = (helpers.FIXTURES / "statusline" / f"{self.RUNTIME}.json").read_text()
        sidecar = sidecar.replace("__SESSION__", SL_SESSION).replace("__CWD__", str(self.work))
        r = helpers.run_ctxcheck(["statusline-tee", "--runtime", self.RUNTIME],
                                 self.bare_env, cwd=self.work, stdin=sidecar)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.sl_env = dict(self.bare_env, **self.ARM_ENV)

    def arm_sl(self, role="worker", *extra):
        r = helpers.run_ctxcheck(["--arm", "--role", role, "--label", LABEL, *extra],
                                 self.sl_env, cwd=self.work)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.marker(SL_SESSION).is_file())

    def assert_empty_object(self, r):
        self.assertEqual(r.stdout, "{}\n")

    def warned(self):
        return json.loads(self.marker(SL_SESSION).read_text()).get("warnedAtPct")


class CursorHookTest(_StatuslineHook):
    RUNTIME = "cursor"
    ARM_ENV = {"CURSOR_AGENT": "1"}

    def cursor_payload(self, **over) -> str:
        # https://cursor.com/docs/hooks.md: postToolUse = common fields +
        # tool_name, tool_input, tool_output, tool_use_id, cwd, duration.
        p = {"conversation_id": SL_SESSION, "generation_id": "gen-synthetic",
             "model": "synthetic-model-cu", "hook_event_name": "postToolUse",
             "cursor_version": "2026.10.08", "workspace_roots": [str(self.work)],
             "user_email": None, "transcript_path": str(self.tmp / "no-such.jsonl"),
             "tool_name": "Shell", "tool_input": {"command": "ls"},
             "tool_output": "{\"stdout\": \"ok\"}", "tool_use_id": "tool-synthetic",
             "cwd": str(self.work), "duration": 12}
        p.update(over)
        return json.dumps(p)

    def additional_context(self, r) -> str:
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"additional_context"})
        return out["additional_context"]

    def test_exact_shape_explicit_and_inferred(self):
        self.arm_sl("worker", "--gate", "1")
        for args in (("--runtime", "cursor"), ()):
            with self.subTest(args=args):
                state = json.loads(self.marker(SL_SESSION).read_text())
                self.marker(SL_SESSION).write_text(json.dumps(dict(state, warnedAtPct=None)))
                msg = self.additional_context(self.hook(self.cursor_payload(), *args))
                self.assertIn("68,000/272,000", msg)
                self.assertIn("PHASE-STATUS: continuation-required", msg)

    def test_session_id_alias(self):
        self.arm_sl("coordinator", "--gate", "1")
        p = json.loads(self.cursor_payload())
        p["session_id"] = p.pop("conversation_id")
        msg = self.additional_context(self.hook(json.dumps(p), "--runtime", "cursor"))
        self.assertIn("references/coordinator-handoff.md", msg)

    def test_under_gate_and_unarmed_print_empty_object(self):
        self.assert_empty_object(self.hook(self.cursor_payload(), "--runtime", "cursor"))
        self.arm_sl("worker")  # 25% against 75% (272K window)
        self.assert_empty_object(self.hook(self.cursor_payload(), "--runtime", "cursor"))
        self.assert_empty_object(self.hook("not json", "--runtime", "cursor"))


class CopilotHookTest(_StatuslineHook):
    RUNTIME = "copilot"
    ARM_ENV = {"COPILOT_AGENT_SESSION_ID": SL_SESSION}

    def camel(self) -> str:
        # docs.github.com/en/copilot/reference/hooks-configuration, event key
        # postToolUse (camelCase).
        return json.dumps({
            "sessionId": SL_SESSION, "timestamp": 1792000000000, "cwd": str(self.work),
            "toolName": "bash", "toolArgs": {"command": "ls"},
            "toolResult": {"resultType": "success", "textResultForLlm": "ok"}})

    def pascal(self) -> str:
        # Event key PostToolUse (PascalCase): VS Code-style snake_case payload.
        return json.dumps({
            "hook_event_name": "PostToolUse", "session_id": SL_SESSION,
            "timestamp": "2026-10-15T12:00:00.000Z", "cwd": str(self.work),
            "tool_name": "bash", "tool_input": {"command": "ls"},
            "tool_result": {"result_type": "success", "text_result_for_llm": "ok"}})

    def additional_context(self, r) -> str:
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"additionalContext"})
        return out["additionalContext"]

    def test_both_casings_explicit_and_inferred(self):
        self.arm_sl("planner", "--gate", "1")
        for casing in ("camel", "pascal"):
            for args in (("--runtime", "copilot"), ()):
                with self.subTest(casing=casing, args=args):
                    state = json.loads(self.marker(SL_SESSION).read_text())
                    self.marker(SL_SESSION).write_text(
                        json.dumps(dict(state, warnedAtPct=None)))
                    msg = self.additional_context(self.hook(getattr(self, casing)(), *args))
                    self.assertIn("61,000/160,000", msg)
                    self.assertIn("references/planner.md", msg)

    def test_under_gate_and_unarmed_print_empty_object(self):
        self.assert_empty_object(self.hook(self.camel(), "--runtime", "copilot"))
        self.arm_sl("worker")  # 38.1% against 75%
        self.assert_empty_object(self.hook(self.camel(), "--runtime", "copilot"))
        self.assert_empty_object(self.hook(self.pascal()))

    def test_claude_payload_is_not_taken_for_copilot(self):
        # Claude's snake_case payload has transcript_path and tool_response.
        from ctxlib import hook
        self.assertEqual(hook._infer(json.loads(self.payload())), "claude")
        self.assertEqual(hook._infer(json.loads(self.pascal())), "copilot")


class AntigravityHookTest(_StatuslineHook):
    RUNTIME = "antigravity"
    ARM_ENV = {"ANTIGRAVITY_CONVERSATION_ID": SL_SESSION}

    def common(self) -> dict:
        # agy-customizations/docs/hooks.md: camelCase common fields.
        return {"conversationId": SL_SESSION, "workspacePaths": [str(self.work)],
                "transcriptPath": str(self.work / ".gemini/antigravity-cli/transcript.jsonl"),
                "artifactDirectoryPath": str(self.work / ".gemini/antigravity-cli/artifacts"),
                "modelName": "auto"}

    def pre_invocation(self) -> str:
        return json.dumps(dict(self.common(), invocationNum=3, initialNumSteps=10))

    def post_tool_use(self) -> str:
        return json.dumps(dict(self.common(), stepIdx=5))

    def ephemeral(self, r) -> str:
        out = json.loads(r.stdout)
        self.assertEqual(set(out), {"injectSteps"})
        self.assertEqual(len(out["injectSteps"]), 1)
        self.assertEqual(set(out["injectSteps"][0]), {"ephemeralMessage"})
        return out["injectSteps"][0]["ephemeralMessage"]

    def test_pre_invocation_injects_an_ephemeral_message(self):
        self.arm_sl("worker", "--gate", "1")
        for args in (("--runtime", "antigravity", "--event", "PreInvocation"),
                     ("--event", "PreInvocation")):
            with self.subTest(args=args):
                state = json.loads(self.marker(SL_SESSION).read_text())
                self.marker(SL_SESSION).write_text(json.dumps(dict(state, warnedAtPct=None)))
                msg = self.ephemeral(self.hook(self.pre_invocation(), *args))
                self.assertIn("125,000/1,000,000", msg)
                self.assertIn("PHASE-STATUS: continuation-required", msg)

    def test_every_other_event_gets_an_empty_object_and_keeps_the_warning(self):
        self.arm_sl("worker", "--gate", "1")
        cases = {
            "PostToolUse": (self.post_tool_use(), ("--event", "PostToolUse")),
            "PostInvocation": (self.pre_invocation(), ("--event", "PostInvocation")),
            "no event named": (self.pre_invocation(), ()),
            "Stop": (json.dumps(dict(self.common(), executionNum=1,
                                     terminationReason="model_stop")), ("--event", "Stop")),
        }
        for name, (stdin, extra) in cases.items():
            with self.subTest(name):
                self.assert_empty_object(self.hook(stdin, "--runtime", "antigravity", *extra))
                self.assertIsNone(self.warned())  # not consumed by a silent event
        self.ephemeral(self.hook(self.pre_invocation(), "--runtime", "antigravity",
                                 "--event", "PreInvocation"))
        self.assertEqual(self.warned(), 12.5)

    def test_under_gate_unarmed_and_garbage_print_empty_object(self):
        args = ("--runtime", "antigravity", "--event", "PreInvocation")
        self.assert_empty_object(self.hook(self.pre_invocation(), *args))
        self.arm_sl("worker")  # 12.5% against 28% (1M window)
        self.assert_empty_object(self.hook(self.pre_invocation(), *args))
        self.assert_empty_object(self.hook("not json {", *args))
        self.assert_empty_object(self.hook("", *args))


if __name__ == "__main__":
    unittest.main()
