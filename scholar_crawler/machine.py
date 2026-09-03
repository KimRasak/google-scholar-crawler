"""One JSON document per run, for callers that are programs rather than people.

An agent researching a topic wants to run this tool once and read the result, not scrape
progress lines out of a terminal. So ``--json`` sends every human line to stderr and prints a
single object on stdout: what was collected, what it cost, where it was written, and — when a
run stopped — what stopped it and what to do about it.

The document is the tool's promised output for programs, so its top-level keys stay put:
``tool``, ``version``, ``ok``, ``exit_code``, ``counts``, ``files``, ``records`` and ``error``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from importlib import metadata
from pathlib import Path
from typing import Any

from .diagnose import Failure

TOOL_KINDS: tuple[str, ...] = (
    "bad_inputs",
    "challenge_unattended",
    "interrupted",
    "missing_since",
    "no_records",
    "runtime_error",
    "unreadable_input",
    "unsupported_mode",
    "usage",
)
"""Failure names this tool raises itself, beyond the ones :class:`Failure` diagnoses."""

KINDS: frozenset[str] = frozenset(TOOL_KINDS) | {member.value for member in Failure}
"""Every ``error.kind`` a document can carry: the vocabulary a caller may branch on."""

DISTRIBUTION = "google-scholar-crawler"
"""Installed name, which is what reports a version."""

FALLBACK_VERSION = "0+source"
"""Reported when the package was never installed, only imported from a checkout."""


def version() -> str:
    """Report the installed version.

    :returns: the distribution version, or a marker when running from a plain checkout.
    """
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return FALLBACK_VERSION


@contextmanager
def human_lines_to_stderr(enabled: bool) -> Iterator[None]:
    """Keep stdout clean for the JSON document.

    Every human line in this tool is a plain ``print``, and each one would corrupt a document
    a caller is parsing. Rather than thread a stream through every module, the whole run is
    redirected once here.

    :param enabled: True in ``--json`` mode; False leaves printing alone.
    :yields: control, with stdout redirected while enabled.
    """
    if not enabled:
        yield
        return
    with redirect_stdout(sys.stderr):
        yield


def emit(document: dict[str, Any]) -> None:
    """Print the run document on the real stdout.

    :param document: the payload to serialize.
    """
    json.dump(document, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _files(paths: dict[str, Path | None]) -> dict[str, str]:
    """Name the files a run wrote.

    :param paths: role to path, where None means the run was not asked for that file.
    :returns: role to path text, skipping the roles that were not asked for.
    """
    return {role: str(path) for role, path in paths.items() if path is not None}


def document(
    *,
    tool: str,
    exit_code: int,
    counts: dict[str, int],
    files: dict[str, Path | None] | None = None,
    records: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the run document.

    :param tool: the command that ran, as installed.
    :param exit_code: the process exit code this run will return.
    :param counts: whole-run counts, such as records and requests.
    :param files: the files the run wrote, by role.
    :param records: the records collected or kept, in output order.
    :param error: what stopped the run, when something did.
    :param extra: tool-specific sections, such as a digest overview.
    :returns: the document to emit.
    """
    payload: dict[str, Any] = {
        "tool": tool,
        "version": version(),
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "counts": counts,
        "files": _files(files or {}),
        "records": records or [],
        "error": error,
    }
    payload.update(extra or {})
    return payload


def failure(kind: str, message: str, next_steps: tuple[str, ...] = ()) -> dict[str, Any]:
    """Describe what stopped a run, for a caller that must decide what to do next.

    :param kind: stable machine name of the failure, such as ``challenge_unattended``.
    :param message: one line naming what happened.
    :param next_steps: concrete actions, most useful first.
    :returns: the error section of a run document.
    :raises ValueError: when ``kind`` is not one of :data:`KINDS`, which would hand a caller a
        name it cannot branch on.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown failure kind {kind!r}; add it to TOOL_KINDS and AGENTS.md")
    return {"kind": kind, "message": message, "next_steps": list(next_steps)}
