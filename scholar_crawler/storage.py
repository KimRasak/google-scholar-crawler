"""Incremental output and resume state.

Results are appended to JSONL as each page is parsed, so a run interrupted by a
challenge, a Ctrl+C or a crash keeps everything already collected. The state file
records the next unfetched offset per query so ``--resume`` continues instead of
re-requesting pages Google already served.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .audit import AuditTally
from .diagnose import CrawlFailure, diagnose_state
from .models import (
    AuthorProfile,
    ScholarResult,
    as_flag,
    as_number,
    as_text,
    describe_signature,
    record_key,
)
from .parser import bibtex_key
from .urls import redact_url

DEFAULT_RECORDS_PATH = Path("out/results.jsonl")
"""Where records land when ``--out`` names nothing."""

DEFAULT_STATE_PATH = Path("out/state.json")
"""Where resume progress lands when ``--state`` names nothing."""

DEFAULT_CHALLENGE_LOG_PATH = Path("out/challenges.jsonl")
"""Where takeovers land when ``--challenge-log`` names nothing."""

PROBE_NAME = ".scholar-write-test"
"""File written and removed to prove a directory accepts writes."""


def absolute(path: Path) -> Path:
    """Resolve a path against the working directory without touching the filesystem.

    :param path: any path a flag carried.
    :returns: the absolute form, so messages name the place that was actually checked.
    """
    return path if path.is_absolute() else Path.cwd() / path


def nearest_existing(directory: Path) -> Path:
    """Find the closest ancestor that exists, so writability can be tested without creating it.

    :param directory: the directory a run would use.
    :returns: ``directory`` itself when it exists, otherwise its closest existing ancestor.
    """
    target = absolute(directory)
    for candidate in (target, *target.parents):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # an ancestor this user may not even look at; keep walking up
            continue
    return Path(target.anchor or ".")


def unwritable(path: Path, *, kind: str = "file") -> str:
    """Say why this run could not write ``path``.

    Checking beats finding out: a run that discovers its output path halfway through has already
    spent the requests it cannot repeat cheaply. The probe writes into the closest existing
    ancestor, so a mistyped path leaves no directories behind.

    :param path: the file a run would write, or the directory itself.
    :param kind: ``file`` when ``path`` is a file, ``dir`` when it is the directory.
    :returns: the reason, or an empty string when the path can be written.
    """
    target = absolute(path)
    if kind == "file" and target.is_dir():
        return f"{path} is a directory, and this run needs to write a file there"
    directory = target if kind == "dir" else target.parent
    existing = nearest_existing(directory)
    for candidate in (directory, *directory.parents):
        if candidate == existing:
            break
        try:
            in_the_way = candidate.exists()
        except OSError:  # an ancestor this user may not even look at; the probe below reports it
            break
        if in_the_way:  # a file: neither a directory to write in, nor one to create under
            if candidate == directory:
                return f"{candidate} is a file, and this run needs a directory there"
            return f"{candidate} is a file, so {directory} cannot be created"
    probe = existing / PROBE_NAME
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        reason = error.strerror or error
        probed = "" if existing == directory else f" (nearest existing: {existing})"
        return f"{directory} cannot be written to: {reason}{probed}"
    return ""


CSV_COLUMNS = (
    "position",
    "title",
    "authors",
    "venue",
    "year",
    "cited_by_count",
    "link",
    "resource_link",
    "resource_type",
    "snippet",
    "cluster_id",
    "cited_by_url",
    "versions_count",
    "versions_url",
    "related_url",
    "citation_only",
    "query",
    "page_start",
    "fetched_at",
)


@dataclass(slots=True)
class ResultSink:
    """Append-only JSONL writer that drops results already seen.

    :param path: JSONL output path; existing content is kept and used for dedup.
    :param tally: audits every written record so a run can report fields that parsed badly.
    :param fresh: the records written this run, in output order, for ``--json`` callers that
        want the result without reading the file back.
    """

    path: Path
    tally: AuditTally = field(default_factory=AuditTally)
    fresh: list[dict[str, Any]] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    _handle: TextIO | None = None
    written: int = 0
    skipped: int = 0

    def open(self) -> None:
        """Load existing keys from ``path`` and open it for appending."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.path.open(encoding="utf-8") as existing:
                for line in existing:
                    line = line.strip()
                    if not line:
                        continue
                    self._seen.add(record_key(json.loads(line)))
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, result: ScholarResult) -> bool:
        """Append ``result`` unless an equal record was already stored.

        :param result: the parsed result.
        :returns: True when the record was new and written.
        """
        assert self._handle is not None, "ResultSink.open() must run before write()"
        key = result.dedup_key()
        if key in self._seen:
            self.skipped += 1
            return False
        self._seen.add(key)
        payload = result.to_dict()
        self.fresh.append(payload)
        self.tally.observe(payload)
        self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._handle.flush()
        self.written += 1
        return True

    def close(self) -> None:
        """Close the underlying file, if open."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


@dataclass(slots=True)
class BibtexSink:
    """BibTeX entries appended to one ``.bib`` file, deduplicated by citation key.

    :param path: ``.bib`` path; created on open, appended to across runs.
    """

    path: Path
    _keys: set[str] = field(default_factory=set)
    _handle: TextIO | None = None
    written: int = 0
    skipped: int = 0

    def open(self) -> None:
        """Read the keys already in the file, then open it for appending."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for entry in self.path.read_text(encoding="utf-8").split("\n@"):
                key = bibtex_key(entry if entry.startswith("@") else f"@{entry}")
                if key:
                    self._keys.add(key)
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, entry: str) -> bool:
        """Append ``entry`` unless its citation key is already stored.

        :param entry: a BibTeX entry.
        :returns: True when it was appended, False when it was a duplicate.
        :raises RuntimeError: when the sink was not opened.
        """
        if self._handle is None:
            raise RuntimeError("BibtexSink.write called before open()")
        key = bibtex_key(entry)
        if key is not None and key in self._keys:
            self.skipped += 1
            return False
        if key is not None:
            self._keys.add(key)
        self._handle.write(entry.strip() + "\n\n")
        self._handle.flush()
        self.written += 1
        return True

    def close(self) -> None:
        """Close the file if it is open."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def profiles_beside(records: Path) -> Path:
    """Name the profile file that belongs to a records file.

    An author crawl parses the profile header anyway, so the file comes free with ``--author``
    and needs no flag of its own. Deriving it from ``--out`` also keeps two crawls writing to
    two different records files from sharing one profile file.

    :param records: the ``--out`` path.
    :returns: the profile path beside it.
    """
    return records.with_suffix(".profiles.jsonl")


@dataclass(frozen=True, slots=True)
class Written:
    """One path a command writes.

    :param label: what it holds, for a reader being told what a run will touch.
    :param flag: the flag naming it, empty for the profile file derived from ``--out``.
    :param path: the path itself.
    :param kind: ``file`` or ``dir``.
    """

    label: str
    flag: str
    path: Path
    kind: str


def written_paths(
    *,
    out: Path,
    state: Path,
    challenge_log: Path,
    profile: Path,
    bibtex: Path | None = None,
    dump_html: Path | None = None,
    authors: bool = False,
) -> list[Written]:
    """List every path a command writes, in the order a reader wants to see them.

    One list answers three questions that must not disagree: what to tell the reader a run will
    touch, which paths to check before spending a request, and which two flags were pointed at
    one path.

    :param out: the ``--out`` records file.
    :param state: the ``--state`` resume file.
    :param challenge_log: the ``--challenge-log`` takeover log.
    :param profile: the ``--profile`` browser profile directory.
    :param bibtex: the ``--bibtex`` export, when asked for.
    :param dump_html: the ``--dump-html`` directory, when asked for.
    :param authors: True when the run crawls author profiles, which writes a file beside ``out``.
    :returns: one entry per path this command writes.
    """
    written = [Written("records", "--out", out, "file")]
    if bibtex is not None:
        written.append(Written("bibtex", "--bibtex", bibtex, "file"))
    if authors:
        written.append(Written("author profiles", "", profiles_beside(out), "file"))
    written.append(Written("resume state", "--state", state, "file"))
    written.append(Written("takeover log", "--challenge-log", challenge_log, "file"))
    if dump_html is not None:
        written.append(Written("page dumps", "--dump-html", dump_html, "dir"))
    written.append(Written("browser profile", "--profile", profile, "dir"))
    return written


@dataclass(slots=True)
class ProfileStore:
    """Author profiles kept as one JSONL record per profile, newest values winning.

    Citation counts change, so a re-crawl replaces the stored record instead of
    appending a second one.

    :param path: JSONL path holding one record per author profile.
    """

    path: Path
    _records: dict[str, dict[str, Any]] = field(default_factory=dict)
    written: int = 0

    def load(self) -> None:
        """Read existing profiles, tolerating a missing file."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as existing:
            for line in existing:
                if line.strip():
                    record = json.loads(line)
                    self._records[record["user_id"]] = record

    def write(self, profile: AuthorProfile) -> None:
        """Store ``profile`` and rewrite the file so each author appears once.

        :param profile: the profile record to keep.
        """
        self._records[profile.dedup_key()] = profile.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as target:
            for record in self._records.values():
                target.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.written += 1


@dataclass(slots=True)
class StateEntry:
    """Stored progress for one target.

    :param signature: the target's resume signature.
    :param next_start: first offset not yet fetched.
    :param exhausted: True when Scholar offered no further page.
    :param updated_at: UTC timestamp of the last update, empty for entries written by
        versions that did not record one.
    """

    signature: str
    next_start: int
    exhausted: bool
    updated_at: str

    @property
    def target(self) -> str:
        """Name the stored target the way every line about it names it.

        :returns: the target and the filters that distinguish it.
        """
        return describe_signature(self.signature)

    def describe(self) -> str:
        """Render the entry as one readable line.

        :returns: the target, its cursor, and when it was last touched.
        """
        status = (
            f"done after {self.next_start} records"
            if self.exhausted
            else f"next offset {self.next_start}"
        )
        seen = self.updated_at.replace("T", " ").replace("+00:00", " UTC") or "unknown time"
        return f"{self.target} — {status}, {seen}"


@dataclass(slots=True)
class StateStore:
    """Per-query pagination cursor persisted as JSON.

    :param path: state file path; created on first save.
    """

    path: Path
    _data: dict[str, dict[str, Any]] = field(default_factory=dict)
    repaired: int = 0

    def load(self) -> None:
        """Read the state file, tolerating a missing one.

        :raises CrawlFailure: when the file exists but is not an object of cursors. Reading it as
            empty would silently re-crawl every target in it, which costs requests to discover.
        """
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CrawlFailure(diagnose_state(self.path, str(error))) from error
        if not isinstance(data, dict):
            raise CrawlFailure(
                diagnose_state(self.path, f"the file holds a {type(data).__name__}, not an object")
            )
        self._data = {}
        self.repaired = 0
        for signature, entry in data.items():
            if not isinstance(entry, dict):
                self.repaired += 1
                continue
            cursor = as_number(entry.get("next_start"))
            exhausted = as_flag(entry.get("exhausted"))
            updated = as_text(entry.get("updated_at"))
            self.repaired += 1 if (cursor, exhausted, updated) != (
                entry.get("next_start"),
                entry.get("exhausted"),
                entry.get("updated_at"),
            ) else 0
            self._data[signature] = {
                "next_start": cursor or 0,
                "exhausted": bool(exhausted),
                "updated_at": updated or "",
            }

    def next_start(self, signature: str, default: int = 0) -> int:
        """Return the next unfetched offset recorded for ``signature``.

        :param signature: query signature from :meth:`SearchRequest.signature`.
        :param default: offset to use when nothing is recorded.
        :returns: the stored offset, or ``default``.
        """
        entry = self._data.get(signature)
        return int(entry["next_start"]) if entry else default  # read as a number by load()

    def record(self, signature: str, next_start: int, *, exhausted: bool = False) -> None:
        """Store progress for ``signature`` and persist the file.

        :param signature: query signature.
        :param next_start: first offset not yet fetched.
        :param exhausted: True when Scholar offered no further page.
        """
        self._data[signature] = {
            "next_start": next_start,
            "exhausted": exhausted,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._save()

    def entries(self) -> list[StateEntry]:
        """List the stored progress, most recently updated first.

        :returns: one entry per stored target.
        """
        entries = [
            StateEntry(
                signature=signature,
                next_start=int(record.get("next_start", 0)),
                exhausted=bool(record.get("exhausted", False)),
                updated_at=str(record.get("updated_at", "")),
            )
            for signature, record in self._data.items()
        ]
        return sorted(entries, key=lambda entry: entry.updated_at, reverse=True)

    def forget(self, pattern: str) -> list[StateEntry]:
        """Drop the stored progress of every target the pattern names.

        Both spellings of a target match: the stored signature (``lang=en``) and the way
        ``--show-state`` prints it (``graph attention [en]``), because what a caller can read
        is what they will type back.

        :param pattern: case-insensitive substring; an empty pattern matches every target.
        :returns: the entries that were removed.
        """
        needle = pattern.casefold()
        removed = [
            entry
            for entry in self.entries()
            if needle in entry.signature.casefold() or needle in entry.target.casefold()
        ]
        for entry in removed:
            del self._data[entry.signature]
        if removed:
            self._save()
        return removed

    def _save(self) -> None:
        """Write the state file, creating its directory when needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


REHEARSAL_TARGET = "rehearsal"
"""``target`` written by a rehearsed takeover; no real listing can be called this."""


@dataclass(slots=True)
class ChallengeRecord:
    """One human takeover, as it happened.

    :param at: UTC timestamp when the challenge was detected.
    :param kind: challenge kind — ``captcha``, ``rate_limit`` or ``consent``.
    :param url: the challenge URL with session material redacted.
    :param reason: what the detector matched.
    :param request_index: which request of this run was blocked, counting this one.
    :param consecutive: how many challenges in a row, counting this one.
    :param waited: seconds spent waiting for the human.
    :param outcome: ``resolved``, ``unattended``, ``budget`` or ``interrupted``.
    :param target: the request that was being loaded, as a short tag.
    :param saw: challenge kinds the window showed while the human worked, in order; empty in
        records written before the wait reported this.
    """

    at: str
    kind: str
    url: str
    reason: str
    request_index: int
    consecutive: int
    waited: float
    outcome: str
    target: str
    saw: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Render the record for JSONL.

        :returns: the record's fields as plain values.
        """
        return {
            "at": self.at,
            "kind": self.kind,
            "url": self.url,
            "reason": self.reason,
            "request_index": self.request_index,
            "consecutive": self.consecutive,
            "waited": round(self.waited, 1),
            "outcome": self.outcome,
            "target": self.target,
            "saw": list(self.saw),
        }

    @property
    def drill(self) -> bool:
        """Whether this record came from ``--rehearse-handoff`` rather than from Scholar.

        A drill nobody attends ends as ``unattended``, and one ended with Ctrl+C as
        ``interrupted``, so the outcome alone cannot tell a drill from a real block. Only the
        target can: a real block always names the listing or profile being fetched.

        :returns: True when this record is a rehearsal.
        """
        return self.outcome == "rehearsed" or self.target == REHEARSAL_TARGET

    def describe(self) -> str:
        """Summarize the takeover in one line.

        :returns: timestamp, kind, outcome and the request it interrupted.
        """
        drill = " (drill)" if self.drill else ""
        streak = f" x{self.consecutive} in a row" if self.consecutive > 1 else ""
        waited = f", waited {self.waited:.0f}s" if self.waited >= 1 else ""
        turned = f", became {' -> '.join(self.saw[1:])}" if len(self.saw) > 1 else ""
        return (
            f"{self.at}  {self.kind}{streak} -> {self.outcome}{drill}{waited}{turned} "
            f"(on request {self.request_index}, loading {self.target})"
        )


@dataclass(slots=True)
class ChallengeLog:
    """Append-only record of every human takeover, for evidence after the fact.

    A challenge is rare, unrepeatable and happens while a human is busy solving it, so the
    run writes down what it saw instead of relying on the terminal scrollback. Session
    material is redacted, so the file is safe to keep and to share.

    :param path: JSONL file; created on the first record.
    """

    path: Path

    def record(
        self,
        *,
        kind: str,
        url: str,
        reason: str,
        request_index: int,
        consecutive: int,
        waited: float,
        outcome: str,
        target: str,
        saw: tuple[str, ...] = (),
    ) -> ChallengeRecord:
        """Append one takeover to the log.

        :param kind: challenge kind.
        :param url: the challenge URL; redacted before it is written.
        :param reason: what the detector matched.
        :param request_index: which request of this run was blocked, counting this one.
        :param consecutive: how many challenges in a row, counting this one.
        :param waited: seconds spent waiting for the human.
        :param outcome: how the takeover ended.
        :param target: the request that was being loaded.
        :param saw: challenge kinds the window showed while the human worked.
        :returns: the record as written.
        """
        entry = ChallengeRecord(
            at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            kind=kind,
            url=redact_url(url),
            reason=reason,
            request_index=request_index,
            consecutive=consecutive,
            waited=waited,
            outcome=outcome,
            target=target,
            saw=saw,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[ChallengeRecord]:
        """Read the log back, oldest first.

        :returns: every readable record; unreadable lines are skipped.
        """
        return self.read()[0]

    def read(self) -> tuple[list[ChallengeRecord], int]:
        """Read the log back with the count of lines that could not be read.

        The log is a plain JSONL file a person may open, trim or move, and a crawl reads it
        before its first request to decide how slowly to go. A line that cannot be read is
        therefore skipped and counted, never guessed at and never a reason to stop.

        :returns: every readable record oldest first, and how many lines were not readable.
        """
        if not self.path.exists():
            return [], 0
        records = []
        unreadable = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                unreadable += 1
                continue
            if not isinstance(data, dict):
                unreadable += 1
                continue
            records.append(
                ChallengeRecord(
                    at=as_text(data.get("at")) or "",
                    kind=as_text(data.get("kind")) or "?",
                    url=as_text(data.get("url")) or "",
                    reason=as_text(data.get("reason")) or "",
                    request_index=as_number(data.get("request_index")) or 0,
                    consecutive=as_number(data.get("consecutive")) or 1,
                    waited=float(as_number(data.get("waited")) or 0.0),
                    outcome=as_text(data.get("outcome")) or "?",
                    target=as_text(data.get("target")) or "",
                    saw=tuple(as_text(kind) or "?" for kind in data.get("saw") or ()),
                )
            )
        return records, unreadable
