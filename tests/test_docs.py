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
# run-plan's wrapper writes the path as a placeholder the runner fills in.
MENTION = re.compile(r"(?:\bctxcheck\b[\"'>]?|<ABS_CTXCHECK>)([^;|&#`\n)]*)")
# An Orca verb follows its command group: `<orca> orchestration worker-start`,
# `orca-ide orchestration check`, or a bare `terminal read` in a code span.
ORCA_VERB = re.compile(r"(?<![\w-])(orchestration|terminal)\s+([a-z][a-z0-9-]*)")
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
