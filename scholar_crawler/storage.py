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
from pathlib import Path
from typing import Any, TextIO

from .models import ScholarResult

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
    """

    path: Path
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
        self._handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
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
        self._data[signature] = {"next_start": next_start, "exhausted": exhausted}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
