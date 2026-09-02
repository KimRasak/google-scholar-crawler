"""Incremental output and resume state.

Results are appended to JSONL as each page is parsed, so a run interrupted by a
challenge, a Ctrl+C or a crash keeps everything already collected. The state file
records the next unfetched offset per query so ``--resume`` continues instead of
re-requesting pages Google already served.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .audit import AuditTally
from .models import AuthorProfile, ScholarResult, describe_signature
from .parser import bibtex_key
from .urls import redact_url

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
    """

    path: Path
    tally: AuditTally = field(default_factory=AuditTally)
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
                    record = json.loads(line)
                    fallback = f"{record.get('title')}::{record.get('link') or ''}"
                    self._seen.add(record.get("cluster_id") or fallback)
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

    def export_csv(self, csv_path: Path) -> int:
        """Write the collected JSONL records to ``csv_path``.

        :param csv_path: destination CSV file, overwritten if present.
        :returns: number of data rows written.
        """
        rows: list[dict[str, Any]] = []
        if self.path.exists():
            with self.path.open(encoding="utf-8") as source:
                rows = [json.loads(line) for line in source if line.strip()]
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)


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
        return f"{describe_signature(self.signature)} — {status}, {seen}"


@dataclass(slots=True)
class StateStore:
    """Per-query pagination cursor persisted as JSON.

    :param path: state file path; created on first save.
    """

    path: Path
    _data: dict[str, dict[str, Any]] = field(default_factory=dict)

    def load(self) -> None:
        """Read the state file, tolerating a missing one."""
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))

    def next_start(self, signature: str, default: int = 0) -> int:
        """Return the next unfetched offset recorded for ``signature``.

        :param signature: query signature from :meth:`SearchRequest.signature`.
        :param default: offset to use when nothing is recorded.
        :returns: the stored offset, or ``default``.
        """
        entry = self._data.get(signature)
        return int(entry["next_start"]) if entry else default

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
        """Drop the stored progress of every target whose signature contains ``pattern``.

        :param pattern: case-insensitive substring; an empty pattern matches every target.
        :returns: the entries that were removed.
        """
        needle = pattern.casefold()
        removed = [entry for entry in self.entries() if needle in entry.signature.casefold()]
        for entry in removed:
            del self._data[entry.signature]
        if removed:
            self._save()
        return removed

    def _save(self) -> None:
        """Write the state file, creating its directory when needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(slots=True)
class ChallengeRecord:
    """One human takeover, as it happened.

    :param at: UTC timestamp when the challenge was detected.
    :param kind: challenge kind — ``captcha``, ``rate_limit`` or ``consent``.
    :param url: the challenge URL with session material redacted.
    :param reason: what the detector matched.
    :param request_index: requests this run had already made.
    :param consecutive: how many challenges in a row, counting this one.
    :param waited: seconds spent waiting for the human.
    :param outcome: ``resolved``, ``unattended``, ``budget`` or ``interrupted``.
    :param target: the request that was being loaded, as a short tag.
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
        }

    def describe(self) -> str:
        """Summarize the takeover in one line.

        :returns: timestamp, kind, outcome and the request it interrupted.
        """
        streak = f" x{self.consecutive} in a row" if self.consecutive > 1 else ""
        waited = f", waited {self.waited:.0f}s" if self.waited >= 1 else ""
        return (
            f"{self.at}  {self.kind}{streak} -> {self.outcome}{waited} "
            f"(after {self.request_index} requests, loading {self.target})"
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
    ) -> ChallengeRecord:
        """Append one takeover to the log.

        :param kind: challenge kind.
        :param url: the challenge URL; redacted before it is written.
        :param reason: what the detector matched.
        :param request_index: requests this run had already made.
        :param consecutive: how many challenges in a row, counting this one.
        :param waited: seconds spent waiting for the human.
        :param outcome: how the takeover ended.
        :param target: the request that was being loaded.
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
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> list[ChallengeRecord]:
        """Read the log back, oldest first.

        :returns: every readable record; unreadable lines are skipped.
        """
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(
                    ChallengeRecord(
                        at=str(data.get("at", "")),
                        kind=str(data.get("kind", "?")),
                        url=str(data.get("url", "")),
                        reason=str(data.get("reason", "")),
                        request_index=int(data.get("request_index", 0)),
                        consecutive=int(data.get("consecutive", 1)),
                        waited=float(data.get("waited", 0.0)),
                        outcome=str(data.get("outcome", "?")),
                        target=str(data.get("target", "")),
                    )
                )
        return records
