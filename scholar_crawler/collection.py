"""A directory of result files as one collection, and what changed since last time.

Collecting a topic over weeks leaves a folder of JSONL files, and two questions that a glob
cannot answer: which files belong to the collection (the previous merge output does not, though
it sits right beside them), and what actually moved since the last merge. Both are answered
here, from local files only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import record_key
from .text import clip

Record = dict[str, Any]

SUFFIX = ".jsonl"
"""Extension the crawler writes, and the only one a collection reads."""


def collection_files(directory: Path, exclude: list[Path] | None = None) -> list[Path]:
    """List the result files of a collection, in a stable order.

    The merged output and any file a run is about to write are excluded, because reading last
    run's merge back in makes a collection look complete while it stands still.

    :param directory: folder holding the crawler's JSONL files.
    :param exclude: files this run writes, which are therefore not inputs.
    :returns: the input files, sorted by name.
    :raises NotADirectoryError: when the path is not a directory.
    """
    if not directory.is_dir():
        raise NotADirectoryError(f"{directory} is not a directory")
    skip = {path.resolve() for path in exclude or []}
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix == SUFFIX and path.resolve() not in skip
    )


def _citations(record: Record) -> int | None:
    """Read a record's citation count.

    :param record: a stored record.
    :returns: the count, or None when the record carries none.
    """
    value = record.get("cited_by_count")
    return value if isinstance(value, int) else None


def _label(record: Record) -> str:
    """Name a record in one short piece of text.

    :param record: a stored record.
    :returns: its title, or the key when it has no title.
    """
    return str(record.get("title") or record_key(record))


@dataclass(slots=True, frozen=True)
class Moved:
    """One work whose citation count changed between two merges.

    :param label: the work's title.
    :param before: citation count in the earlier set.
    :param after: citation count now.
    """

    label: str
    before: int
    after: int

    @property
    def change(self) -> int:
        """Report the movement.

        :returns: citations gained, negative when the count fell.
        """
        return self.after - self.before

    def describe(self) -> str:
        """Summarize the movement in one line.

        :returns: the change, the new total and the title.
        """
        sign = "+" if self.change > 0 else ""
        return f"  {sign}{self.change:>6}  now {self.after:>8,}  {clip(self.label, 60)}"


@dataclass(slots=True)
class Delta:
    """What changed between an earlier merged set and the current one.

    :param added: works present now and absent before.
    :param gone: works present before and absent now.
    :param moved: works whose citation count changed, largest movement first.
    :param same: works present in both with an unchanged count.
    :param reclustered: titles listed as both added and gone, which is Scholar giving one work
        a new id rather than two works changing hands.
    :param before_total: records in the earlier set.
    :param after_total: records in the current set.
    """

    added: list[str]
    gone: list[str]
    moved: list[Moved]
    same: int
    reclustered: list[str]
    before_total: int
    after_total: int

    @property
    def citations_gained(self) -> int:
        """Report the collection's net citation movement.

        :returns: citations gained across works present in both sets.
        """
        return sum(item.change for item in self.moved)

    def quiet(self) -> bool:
        """Report whether anything changed at all.

        :returns: True when nothing was added, lost or moved.
        """
        return not self.added and not self.gone and not self.moved


def compare(before: list[Record], after: list[Record]) -> Delta:
    """Compare two merged sets of records.

    Both sides are keyed the way the crawler deduplicates, so a work counts as the same work
    across runs even when its citation count, venue or snippet changed.

    :param before: records from the earlier merge.
    :param after: records from the current merge.
    :returns: what changed.
    """
    old = {record_key(record): record for record in before}
    new = {record_key(record): record for record in after}
    added = sorted(_label(new[key]) for key in new.keys() - old.keys())
    gone = sorted(_label(old[key]) for key in old.keys() - new.keys())
    moved = []
    same = 0
    for key in old.keys() & new.keys():
        was, now = _citations(old[key]), _citations(new[key])
        if now is None or was == now:
            # A count that disappeared is information Scholar stopped showing, not a fall to
            # zero, so it is not a movement. A count that appeared is the opposite: Scholar
            # omits the citing-works link until a work has one, so absent means none yet.
            same += 1
            continue
        moved.append(Moved(label=_label(new[key]), before=was or 0, after=now))
    moved.sort(key=lambda item: (-abs(item.change), item.label))
    return Delta(
        added=added,
        gone=gone,
        moved=moved,
        same=same,
        reclustered=sorted(set(added) & set(gone)),
        before_total=len(old),
        after_total=len(new),
    )


def render_delta(delta: Delta, *, top: int = 5, since: Path | None = None) -> list[str]:
    """Report a comparison in the terminal.

    :param delta: the comparison to report.
    :param top: how many added, lost and moved works to list.
    :param since: the file the earlier set was read from, named in the first line.
    :returns: printable lines.
    """
    source = f" since {since}" if since is not None else ""
    if delta.quiet():
        return [f"nothing changed{source}: the same {delta.after_total} works, same counts"]
    lines = [
        f"{delta.before_total} works{source} -> {delta.after_total} now: "
        f"{len(delta.added)} new, {len(delta.gone)} no longer here, "
        f"{len(delta.moved)} with a new citation count"
    ]
    if delta.reclustered:
        # The same title on both sides: one work with a new card id counts as gone and added,
        # and reading that as two works overstates how much the collection churned.
        count = len(delta.reclustered)
        lines.append(
            "1 work appears as both new and gone under one title: Scholar re-clustered it, "
            "so one work with a new id counts as both"
            if count == 1
            else f"{count} works appear as both new and gone under one title each: "
            "Scholar re-clustered them, so each counts as both"
        )
    if delta.moved:
        gained = delta.citations_gained
        lines.append(f"citations gained across the works in both: {gained:+,}")
        lines.append("biggest movers:")
        lines.extend(item.describe() for item in delta.moved[:top])
    for title, works in (("new:", delta.added), ("no longer here:", delta.gone)):
        if not works:
            continue
        lines.append(title)
        lines.extend(f"  {clip(label, 70)}" for label in works[:top])
        if len(works) > top:
            lines.append(f"  ... and {len(works) - top} more")
    if delta.gone:
        lines.append(
            "a work goes missing when its file was removed or a filter now excludes it, or "
            "when Scholar re-clustered it under a new id; not because Scholar dropped it"
        )
    return lines
