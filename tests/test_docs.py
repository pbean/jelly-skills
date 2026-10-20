"""The skills' Markdown stays in sync with ctxcheck and with the repo layout."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

import helpers

SKILLS = helpers.REPO / "skills"
FLAG = re.compile(r"--[a-z][a-z0-9-]*")
SUBCOMMAND = re.compile(r"[a-z]+(?:-[a-z]+)+")
# A ctxcheck mention runs to the end of its code span or shell command.
# run-plan's wrapper writes the path as a placeholder the runner fills in,
# and its SKILL.md as the path recorded in run-state.json.
MENTION = re.compile(r"(?:\bctxcheck\b[\"'>]?|<ABS_CTXCHECK>|<ctxcheck_path>)([^;|&#`\n)]*)")
# An Orca verb follows its command group: `<orca> orchestration worker-start`,
# `orca-ide orchestration check`, or a bare `terminal read` in a code span.
ORCA_VERB = re.compile(r"(?<![\w-])(orchestration|terminal)\s+([a-z][a-z0-9-]*)")
# A herdr command names its group, then its verb: `herdr agent start <name> ...`.
# `herdr status` has no verb, and a bare `herdr agent` names the group's listing.
HERDR_VERB = re.compile(r"(?<![\w-])herdr\s+(agent|pane|tab|workspace|integration|status)\b"
                        r"(?:\s+([a-z][a-z-]*))?")
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE = re.compile(r"`([^`\n]+)`")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _markdown() -> list[Path]:
    return sorted(SKILLS.rglob("*.md"))


def _split(text: str) -> tuple[str, list[str], str]:
    """Return (frontmatter, fenced code lines, prose with code removed)."""
    front = ""
    if text.startswith("---\n"):
        end = text.index("\n---", 4)
        front, text = text[4:end], text[end + 4:]
    code, prose, fenced = [], [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        (code if fenced else prose).append(line)
    return front, code, "\n".join(prose)


def _code_snippets(text: str) -> list[str]:
    """Every piece of a file that is code: frontmatter, fenced lines, inline spans."""
    front, code, prose = _split(text)
    return [front, *code, *INLINE.findall(prose)]


_TMP = tempfile.TemporaryDirectory()


def tearDownModule():
    _TMP.cleanup()


@lru_cache(maxsize=None)
def _help(*args: str) -> str:
    r = helpers.run_ctxcheck([*args, "--help"], helpers.clean_env(_TMP.name))
    return r.stdout + r.stderr


@lru_cache(maxsize=None)
def _subcommands() -> frozenset:
    """Subcommands named by `ctxcheck --help`, plus any with their own usage."""
    names = set(re.findall(r"ctxcheck (" + SUBCOMMAND.pattern + ")", _help()))
    return frozenset(names)


def _subcommand_exists(name: str) -> bool:
    return name in _subcommands() or f"usage: ctxcheck {name}" in _help(name)


def _subcommand_flags(name: str) -> set:
    """Flags from the subcommand's own --help and its usage in the main help.

    statusline-tee passes --help through to the wrapped status line, so for
    it the main help's usage block is the only source."""
    flags = set(FLAG.findall(_help(name)))
    lines = _help().splitlines()
    for i, line in enumerate(lines):
        if f"ctxcheck {name}" in line:
            flags |= set(FLAG.findall(line))
            # A usage entry wraps its options onto indented follow-on lines.
            for cont in lines[i + 1:]:
                if "ctxcheck" in cont or not cont.startswith(" " * 13):
                    break
                flags |= set(FLAG.findall(cont))
    return flags


def mentions() -> list[tuple[Path, str | None, set]]:
    """(file, subcommand or None, flags) for each ctxcheck invocation in the docs."""
    found = []
    for path in _markdown():
        for snippet in _code_snippets(path.read_text()):
            for rest in MENTION.findall(snippet):
                words = rest.split()
                sub = words[0] if words and SUBCOMMAND.fullmatch(words[0]) else None
                found.append((path, sub, set(FLAG.findall(rest))))
    return found


class HelpSanityTest(unittest.TestCase):
    def test_main_help_lists_flags_and_subcommands(self):
        self.assertIn("--arm", _help())
        self.assertIn("install-hook", _subcommands())

    def test_subcommand_help_is_its_own(self):
        self.assertIn("usage: ctxcheck install-hook", _help("install-hook"))

    def test_scanner_finds_invocations(self):
        found = mentions()
        self.assertTrue(any(sub == "install-hook" for _, sub, _f in found))
        self.assertTrue(any("--arm" in f for _, _s, f in found))


class FlagsExistTest(unittest.TestCase):
    """(a) every ctxcheck flag and subcommand the docs name exists."""

    def test_subcommands_exist(self):
        for path, sub, _flags in mentions():
            if sub:
                with self.subTest(file=str(path.relative_to(helpers.REPO)), sub=sub):
                    self.assertTrue(_subcommand_exists(sub),
                                    f"ctxcheck has no subcommand {sub!r}")

    def test_flags_exist(self):
        main = set(FLAG.findall(_help()))
        for path, sub, flags in mentions():
            known = _subcommand_flags(sub) if sub else main
            for flag in sorted(flags - {"--help"}):
                with self.subTest(file=str(path.relative_to(helpers.REPO)),
                                  sub=sub, flag=flag):
                    self.assertIn(flag, known,
                                  f"ctxcheck {sub or ''} has no flag {flag}")

    def test_bare_flags_in_context_budget_docs(self):
        """An inline `--flag` in context-budget's own docs is a ctxcheck flag."""
        known = set(FLAG.findall(_help()))
        for name in _subcommands():
            known |= _subcommand_flags(name)
        for path in sorted((SKILLS / "context-budget").rglob("*.md")):
            _front, _code, prose = _split(path.read_text())
            for span in INLINE.findall(prose):
                if not span.startswith("--"):
                    continue
                for flag in FLAG.findall(span):
                    with self.subTest(file=path.name, flag=flag):
                        self.assertIn(flag, known)


def _wrapper_suffix() -> str:
    """plan-format.md §7: the canonical wrapper the runner puts in every phase file."""
    text = (SKILLS / "run-plan" / "references" / "plan-format.md").read_text()
    start = text.index("## 7. Wrapper suffix")
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


class WrapperBudgetTest(unittest.TestCase):
    """The wrapper's ctxcheck commands are real ctxcheck invocations."""

    def test_wrapper_arms_checks_and_disarms(self):
        found = [set(FLAG.findall(rest)) for snippet in _code_snippets(_wrapper_suffix())
                 for rest in MENTION.findall(snippet)]
        flags = set().union(*found) if found else set()
        for flag in ("--arm", "--role", "--label", "--gate", "--disarm"):
            self.assertIn(flag, flags)
        self.assertIn(set(), found, "the wrapper has no plain boundary check")
        # The same scan feeds FlagsExistTest; check here too so a wrapper
        # flag can't hide behind an unrelated subTest.
        main = set(FLAG.findall(_help()))
        self.assertLessEqual(flags, main)


@lru_cache(maxsize=None)
def _orca_commands(group: str) -> dict:
    """{verb: summary} from `orca-ide <group> --help`'s Commands block."""
    r = subprocess.run(["orca-ide", group, "--help"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    commands, listing = {}, False
    for line in (r.stdout + r.stderr).splitlines():
        if line.strip() == "Commands:":
            listing = True
            continue
        m = re.match(r"^\s+([a-z][a-z0-9-]*)\s{2,}(.*)$", line)
        if listing and m:
            commands[m.group(1)] = m.group(2)
        elif listing and line.strip():
            listing = False
    return commands


def orca_verbs() -> list[tuple[Path, str, str]]:
    """(file, group, verb) for each Orca command run-plan's docs name in code."""
    found = []
    for path in sorted((SKILLS / "run-plan").rglob("*.md")):
        _front, code, prose = _split(path.read_text())
        for snippet in [*code, *INLINE.findall(prose)]:
            for group, verb in ORCA_VERB.findall(snippet):
                found.append((path, group, verb))
    return found


class OrcaVerbsTest(unittest.TestCase):
    """Every orchestration and terminal verb run-plan names is a live one."""

    def test_scanner_finds_verbs(self):
        verbs = {(g, v) for _p, g, v in orca_verbs()}
        self.assertTrue(verbs, "no Orca verbs extracted from skills/run-plan")
        self.assertIn(("orchestration", "worker-start"), verbs)
        self.assertIn(("terminal", "send"), verbs)

    @unittest.skipIf(shutil.which("orca-ide") is None,
                     "orca-ide is not installed; can't check Orca verbs")
    def test_verbs_exist(self):
        found = orca_verbs()
        self.assertTrue(found, "no Orca verbs extracted from skills/run-plan")
        for group in ("orchestration", "terminal"):
            self.assertTrue(_orca_commands(group), f"orca-ide {group} --help listed nothing")
        for path, group, verb in found:
            with self.subTest(file=str(path.relative_to(helpers.REPO)), verb=f"{group} {verb}"):
                commands = _orca_commands(group)
                self.assertIn(verb, commands, f"orca-ide {group} has no verb {verb!r}")
                self.assertFalse(commands[verb].startswith("Retired"),
                                 f"orca-ide {group} {verb} is retired")


def _run_herdr(*args: str) -> str:
    """Output of a read-only herdr call. Never bare `herdr`: that launches its TUI."""
    assert args, "bare herdr launches the TUI"
    r = subprocess.run(["herdr", *args], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    return r.stdout + r.stderr


@lru_cache(maxsize=None)
def _herdr_usage(group: str) -> dict:
    """{verb: usage lines} from `herdr <group>`'s listing.

    `herdr <group> <verb> --help` prints the top-level help, so the group's
    own listing (`  herdr <group> <verb> ...`, one or more lines per verb) is
    the only per-verb usage."""
    usage = {}
    for line in _run_herdr(group).splitlines():
        m = re.match(rf"^\s+herdr {group} ([a-z][a-z-]*)\b(.*)$", line)
        if m:
            usage[m.group(1)] = (usage.get(m.group(1), "") + " " + line.strip()).strip()
    return usage


@lru_cache(maxsize=None)
def _herdr_global_flags() -> frozenset:
    """Flags in the top-level help's Options block, which any command may take."""
    text = _run_herdr("--help")
    return frozenset(FLAG.findall(text[text.find("\nOptions:"):]))


HERDR_DISPATCH = SKILLS / "run-plan" / "references" / "dispatch-herdr.md"


def herdr_commands(path: Path = HERDR_DISPATCH) -> list[tuple[str, str, set]]:
    """(group, verb, flags) for each herdr command a doc names in code.

    A command's flags run to the end of its snippet or shell command; what
    follows a lone `--` belongs to the agent, not to herdr."""
    found = []
    _front, code, prose = _split(path.read_text())
    for snippet in [*code, *INLINE.findall(prose)]:
        for m in HERDR_VERB.finditer(snippet):
            rest = re.sub(r'"[^"]*"', '""', snippet[m.end():])  # a quoted prompt is one word
            rest = re.split(r"[;|&'`)]", rest, maxsplit=1)[0]
            rest = re.split(r"(?<!\S)--(?!\S)", rest, maxsplit=1)[0]
            found.append((m.group(1), m.group(2) or "", set(FLAG.findall(rest))))
    return found


class HerdrVerbsTest(unittest.TestCase):
    """Every herdr command dispatch-herdr.md names is a live one, with live flags."""

    def test_scanner_finds_verbs(self):
        found = herdr_commands()
        verbs = {(g, v) for g, v, _f in found}
        self.assertIn(("agent", "start"), verbs)
        self.assertIn(("tab", "create"), verbs)
        self.assertIn(("agent", "prompt"), verbs)
        flags = set().union(*(f for _g, _v, f in found))
        for flag in ("--until", "--kind", "--pane", "--no-focus", "--source"):
            self.assertIn(flag, flags)
        # The agent's own arguments after `--` are not herdr flags.
        self.assertNotIn("--model", flags)

    @unittest.skipIf(shutil.which("herdr") is None,
                     "herdr is not installed; can't check herdr verbs")
    def test_verbs_exist(self):
        found = herdr_commands()
        self.assertTrue(found, "no herdr commands extracted from dispatch-herdr.md")
        for group, verb, _flags in found:
            if group == "status" or not verb:  # a group's bare listing, or `herdr status`
                continue
            with self.subTest(command=f"herdr {group} {verb}"):
                usage = _herdr_usage(group)
                self.assertTrue(usage, f"herdr {group} listed nothing")
                self.assertIn(verb, usage, f"herdr {group} has no verb {verb!r}")

    @unittest.skipIf(shutil.which("herdr") is None,
                     "herdr is not installed; can't check herdr flags")
    def test_flags_exist(self):
        for group, verb, flags in herdr_commands():
            if group == "status" or not verb:
                continue
            usage = _herdr_usage(group).get(verb, "")
            known = set(FLAG.findall(usage)) | _herdr_global_flags()
            for flag in sorted(flags):
                with self.subTest(command=f"herdr {group} {verb}", flag=flag):
                    self.assertIn(flag, known, f"herdr {group} {verb} has no flag {flag}")


def _section(path: Path, heading: str) -> str:
    text = path.read_text()
    start = text.index(heading)
    end = text.find("\n## ", start + 1)
    return text[start:] if end == -1 else text[start:end]


RUN_PLAN = SKILLS / "run-plan"
HANDOFF = RUN_PLAN / "references" / "coordinator-handoff.md"
DISPATCH_FILES = sorted((RUN_PLAN / "references").glob("dispatch-*.md"))
DISPATCH_ORCA = RUN_PLAN / "references" / "dispatch-orca.md"
# Names that would tie backend-neutral doctrine to a backend: Orca's, then herdr's.
BACKEND_NAMES = re.compile(r"\borca\b|orca-ide|\borchestration\b|consumer_fenced|run_required"
                           r"|--ack\b|\b(?:run-(?:use|create|current|list|show)"
                           r"|task-(?:create|list|update)|worker-[a-z]+|gate-[a-z]+)\b"
                           r"|\bherdr\b|\bherdr_\w+|\bagent_(?:not_\w+|prompt_\w+|blocked"
                           r"|started|name_taken)\b",
                           re.IGNORECASE)


class CoordinatorSuccessionDocsTest(unittest.TestCase):
    """The handoff doctrine is backend-neutral, dispatch-orca §9 holds the
    Orca half, and run-plan's ctxcheck calls are real ones."""

    def test_handoff_names_no_backend_names(self):
        text = HANDOFF.read_text()
        self.assertEqual(BACKEND_NAMES.findall(text), [])
        # In code only: prose says "the successor's terminal is ...".
        self.assertEqual([v for s in _code_snippets(text) for v in ORCA_VERB.findall(s)], [])

    def test_handoff_names_no_herdr_commands(self):
        text = HANDOFF.read_text()
        self.assertEqual([m.group(0) for s in _code_snippets(text)
                          for m in HERDR_VERB.finditer(s)], [])

    @unittest.skipIf(shutil.which("orca-ide") is None,
                     "orca-ide is not installed; can't list Orca verbs")
    def test_handoff_names_no_live_orca_verb(self):
        text = HANDOFF.read_text()
        for group in ("orchestration", "terminal"):
            for verb in _orca_commands(group):
                if "-" in verb:  # plain words (check, send, read) are prose too
                    with self.subTest(verb=verb):
                        self.assertNotRegex(text, rf"(?<![\w-]){re.escape(verb)}(?![\w-])")

    def test_section_9_verbs(self):
        section = _section(DISPATCH_ORCA, "## 9. Coordinator succession")
        _front, code, prose = _split(section)
        verbs = {gv for snippet in [*code, *INLINE.findall(prose)]
                 for gv in ORCA_VERB.findall(snippet)}
        for want in (("terminal", "show"), ("terminal", "create"), ("terminal", "wait"),
                     ("terminal", "send"), ("terminal", "read"), ("orchestration", "check"),
                     ("orchestration", "run-use")):
            self.assertIn(want, verbs)
        if shutil.which("orca-ide") is None:
            self.skipTest("orca-ide is not installed; verbs extracted but not checked")
        for group, verb in sorted(verbs):
            with self.subTest(verb=f"{group} {verb}"):
                self.assertIn(verb, _orca_commands(group))

    def test_herdr_section_9_verbs(self):
        section = _section(HERDR_DISPATCH, "## 9. Coordinator succession")
        _front, code, prose = _split(section)
        verbs = {(m.group(1), m.group(2)) for snippet in [*code, *INLINE.findall(prose)]
                 for m in HERDR_VERB.finditer(snippet) if m.group(2)}
        for want in (("pane", "current"), ("tab", "create"), ("agent", "start"),
                     ("pane", "run"), ("agent", "get"), ("agent", "rename"),
                     ("agent", "wait"), ("agent", "prompt"), ("agent", "read")):
            self.assertIn(want, verbs)
        if shutil.which("herdr") is None:
            self.skipTest("herdr is not installed; verbs extracted but not checked")
        for group, verb in sorted(verbs):
            with self.subTest(verb=f"herdr {group} {verb}"):
                self.assertIn(verb, _herdr_usage(group))

    def test_section_9_terminal_verbs_name_their_terminal(self):
        """Without --terminal, Orca targets the worktree's active terminal."""
        section = _section(DISPATCH_ORCA, "## 9. Coordinator succession")
        _front, code, _prose = _split(section)
        for line in code:
            m = re.search(r"terminal (show|wait|send|read)\b", line)
            if m:
                with self.subTest(line=line.strip()):
                    self.assertIn("--terminal", line)

    def test_run_plan_ctxcheck_calls(self):
        found = [(p, sub, flags) for p, sub, flags in mentions()
                 if RUN_PLAN in p.parents]
        subs = {sub for _p, sub, _f in found}
        flags = set().union(*(f for _p, _s, f in found))
        self.assertIn("launch-command", subs)
        for flag in ("--arm", "--role", "--label", "--gate", "--disarm"):
            self.assertIn(flag, flags)
        main = set(FLAG.findall(_help()))
        for path, sub, fl in found:
            known = _subcommand_flags(sub) if sub else main
            with self.subTest(file=path.name, sub=sub, flags=sorted(fl)):
                if sub:
                    self.assertTrue(_subcommand_exists(sub))
                self.assertLessEqual(fl - {"--help"}, known)

    def test_skill_md_wires_succession(self):
        text = (RUN_PLAN / "SKILL.md").read_text()
        for needle in ("--role coordinator", "takeover_pending", "coordinator_generation",
                       "handoffs[]", "references/coordinator-handoff.md", "--disarm"):
            self.assertIn(needle, text)



class DispatchShapeTest(unittest.TestCase):
    """Every dispatch backend file has the sections SKILL.md cites by number,
    and SKILL.md itself names no backend outside "## Dispatch backends"."""

    def test_dispatch_files_share_sections(self):
        self.assertTrue(DISPATCH_FILES, "no references/dispatch-*.md found")
        for path in DISPATCH_FILES:
            with self.subTest(file=path.name):
                text = path.read_text()
                numbers = [int(n) for n in re.findall(r"^## (\d)\. ", text, re.MULTILINE)]
                self.assertEqual(numbers, list(range(10)))
                for sub in ("8a", "8b"):
                    self.assertRegex(text, rf"(?m)^### {sub}\. ")

    def test_skill_md_is_backend_neutral(self):
        path = RUN_PLAN / "SKILL.md"
        heading = "## Dispatch backends"
        # The frontmatter's description and compatibility name the backends on purpose.
        _front, code, prose = _split(path.read_text().replace(_section(path, heading), ""))
        body = "\n".join([*code, prose])
        self.assertEqual(BACKEND_NAMES.findall(body), [])
        self.assertEqual([v for s in [*code, *INLINE.findall(prose)]
                          for v in ORCA_VERB.findall(s)], [])

def frontmatter(path: Path) -> tuple[dict, dict]:
    """Top-level keys and the metadata block's keys, from simple YAML."""
    front, _code, _prose = _split(path.read_text())
    top, meta, in_meta = {}, {}, False
    for line in front.splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            top[m.group(1)] = m.group(2)
            in_meta = m.group(1) == "metadata"
            continue
        m = re.match(r"^\s+([A-Za-z_-]+):\s*(.*)$", line)
        if in_meta and m:
            meta[m.group(1)] = m.group(2).strip("\"'")
    return top, meta


class FrontmatterTest(unittest.TestCase):
    """(b) each SKILL.md has name, description, license and metadata.version."""

    def test_skill_frontmatter(self):
        skills = sorted(SKILLS.glob("*/SKILL.md"))
        self.assertGreaterEqual(len(skills), 3)
        for path in skills:
            top, meta = frontmatter(path)
            with self.subTest(skill=path.parent.name):
                for key in ("name", "description", "license", "metadata"):
                    self.assertIn(key, top)
                self.assertEqual(top["name"], path.parent.name)
                self.assertRegex(meta.get("version", ""), r"^\d+\.\d+\.\d+$")


class LinksTest(unittest.TestCase):
    """(c) relative links in the skills' Markdown resolve."""

    def test_relative_links_resolve(self):
        for path in _markdown():
            _front, _code, prose = _split(path.read_text())
            prose = INLINE.sub("", prose)
            for target in LINK.findall(prose):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                with self.subTest(file=str(path.relative_to(helpers.REPO)), link=target):
                    self.assertTrue((path.parent / target.split("#")[0]).exists())


if __name__ == "__main__":
    unittest.main()
