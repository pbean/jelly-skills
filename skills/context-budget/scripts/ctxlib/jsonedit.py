"""Surgical edits to a JSON settings file that keep everything else byte-for-byte.

Agent settings files are hand-edited: re-serializing one would reorder nothing
(dicts keep order) but would still reflow arrays, change indentation and drop
the user's formatting. Instead, parse() maps every value to its span in the
text, and the edit helpers splice only the bytes that change. An insertion is
placed so that removing the same member restores the original text exactly,
which is what lets `--uninstall` undo an install.

Strict JSON only: text json.loads() rejects (comments, trailing commas) is
refused by the caller rather than guessed at.
"""

from __future__ import annotations

import json
import re
from typing import List, NamedTuple, Optional, Tuple

_WS = re.compile(r"[ \t\r\n]*")
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"', re.S)
_SCALAR = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null")


class Member(NamedTuple):
    key: str
    key_start: int
    value: "Node"


class Node(NamedTuple):
    kind: str  # "object" | "array" | "string" | "scalar"
    start: int
    end: int  # exclusive
    members: Tuple[Member, ...] = ()

    def get(self, key: str) -> Optional[Member]:
        for m in self.members:
            if m.key == key:
                return m
        return None


def parse(text: str) -> Node:
    """Span tree of `text`. Raises ValueError if it isn't strict JSON."""
    json.loads(text)  # validate first; the scanner below assumes valid input
    node, end = _value(text, _WS.match(text, 0).end())
    return node


def _value(text: str, i: int) -> Tuple[Node, int]:
    c = text[i]
    if c == "{":
        members: List[Member] = []
        start = i
        i = _WS.match(text, i + 1).end()
        while text[i] != "}":
            m = _STRING.match(text, i)
            key = json.loads(m.group())
            i = _WS.match(text, m.end()).end() + 1  # past ':'
            child, i = _value(text, _WS.match(text, i).end())
            members.append(Member(key, m.start(), child))
            i = _WS.match(text, i).end()
            if text[i] == ",":
                i = _WS.match(text, i + 1).end()
        return Node("object", start, i + 1, tuple(members)), i + 1
    if c == "[":
        start = i
        i = _WS.match(text, i + 1).end()
        while text[i] != "]":
            _, i = _value(text, i)
            i = _WS.match(text, i).end()
            if text[i] == ",":
                i = _WS.match(text, i + 1).end()
        return Node("array", start, i + 1), i + 1
    if c == '"':
        m = _STRING.match(text, i)
        return Node("string", i, m.end()), m.end()
    m = _SCALAR.match(text, i)
    return Node("scalar", i, m.end()), m.end()


def newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def indent_unit(text: str) -> str:
    """The file's indentation step, taken from its first indented line."""
    m = re.search(r"^([ \t]+)\S", text, re.M)
    return m.group(1) if m else "  "


def _line_indent(text: str, pos: int) -> str:
    line_start = text.rfind("\n", 0, pos) + 1
    return re.match(r"[ \t]*", text[line_start:]).group()


def _render(value: object, indent: str, unit: str, nl: str) -> str:
    """`value` as JSON laid out like the file, continuing lines at `indent`."""
    out = json.dumps(value, indent=unit, ensure_ascii=False)
    return out.replace("\n", nl + indent)


def set_value(text: str, node: Node, value: object) -> str:
    """Replace one value, leaving every other byte alone."""
    unit, nl = indent_unit(text), newline(text)
    rendered = _render(value, _line_indent(text, node.start), unit, nl)
    return text[:node.start] + rendered + text[node.end:]


def add_member(text: str, obj: Node, key: str, value: object) -> str:
    """Append `"key": value` as the object's last member, in the file's style."""
    unit, nl = indent_unit(text), newline(text)
    if obj.members:
        last = obj.members[-1]
        first = obj.members[0]
        if "\n" in text[obj.start:first.key_start]:
            indent = _line_indent(text, first.key_start)
            sep = "," + nl + indent
        else:  # a one-line object stays on one line
            indent, sep = "", ", "
        body = json.dumps(key) + ": " + (
            _render(value, indent, unit, nl) if indent else json.dumps(value, ensure_ascii=False))
        at = last.value.end
        return text[:at] + sep + body + text[at:]
    outer = _line_indent(text, obj.start)
    indent = outer + unit
    body = json.dumps(key) + ": " + _render(value, indent, unit, nl)
    return text[:obj.start] + "{" + nl + indent + body + nl + outer + "}" + text[obj.end:]


def remove_member(text: str, obj: Node, key: str) -> str:
    """Delete one member. Undoes add_member exactly when it appended it."""
    idx = next(i for i, m in enumerate(obj.members) if m.key == key)
    member = obj.members[idx]
    if len(obj.members) == 1:
        return text[:obj.start] + "{}" + text[obj.end:]
    if idx > 0:  # from the end of the previous value through this one
        return text[:obj.members[idx - 1].value.end] + text[member.value.end:]
    return text[:member.key_start] + text[obj.members[1].key_start:]
