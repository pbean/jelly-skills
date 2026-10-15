"""ctxlib - the implementation behind ctxcheck. Python 3.9+ stdlib only."""

from __future__ import annotations

__version__ = "0.1.0"

# Exit codes, shared by every entry point.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNMEASURABLE = 3
EXIT_GATE_EXCEEDED = 10


class CtxError(Exception):
    """A user-facing failure; the CLI prints it and exits EXIT_ERROR."""


class Unmeasurable(CtxError):
    """No runtime's session data resolved; the CLI exits EXIT_UNMEASURABLE.

    `tried` holds one human-readable line per candidate that was considered.
    """

    def __init__(self, message: str, tried: list[str] | None = None) -> None:
        super().__init__(message)
        self.tried = list(tried or [])
