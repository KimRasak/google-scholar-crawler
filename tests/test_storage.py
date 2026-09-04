"""Incremental output and resume state."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.diagnose import CrawlFailure, Failure  # noqa: E402
from scholar_crawler.parser import parse_result_page  # noqa: E402
from scholar_crawler.storage import (  # noqa: E402
    PROBE_NAME,
    ResultSink,
    StateStore,
    nearest_existing,
    profiles_beside,
    unwritable,
)
from tests.fixtures import RESULT_PAGE_HTML  # noqa: E402


def _sink(tmp_path: Path) -> ResultSink:
    sink = ResultSink(tmp_path / "out" / "results.jsonl")
    sink.open()
    return sink


def test_writes_records_and_skips_duplicates(tmp_path: Path) -> None:
    results = parse_result_page(RESULT_PAGE_HTML).results
    sink = _sink(tmp_path)
    assert [sink.write(result) for result in results] == [True, True, True]
    assert [sink.write(result) for result in results] == [False, False, False]
    sink.close()
    assert sink.written == 3
    assert sink.skipped == 3
    lines = (tmp_path / "out" / "results.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["title"] == "Attention is all you need"


def test_a_record_is_on_disk_before_the_run_ends(tmp_path: Path) -> None:
    # The promise is that Ctrl+C, a crash or a timed-out takeover never loses what was
    # already collected, which holds only if each record reaches the file as it is written.
    results = parse_result_page(RESULT_PAGE_HTML).results
    sink = _sink(tmp_path)
    sink.write(results[0])
    mid_run = (tmp_path / "out" / "results.jsonl").read_text(encoding="utf-8")
    assert json.loads(mid_run.strip())["title"] == "Attention is all you need"
    sink.write(results[1])
    assert len((tmp_path / "out" / "results.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 2
    sink.close()


def test_reopening_dedups_against_existing_file(tmp_path: Path) -> None:
    results = parse_result_page(RESULT_PAGE_HTML).results
    first = _sink(tmp_path)
    first.write(results[0])
    first.close()
    second = _sink(tmp_path)
    assert second.write(results[0]) is False
    assert second.write(results[1]) is True
    second.close()


def test_state_round_trip(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.json")
    state.load()
    assert state.next_start("sig", default=0) == 0
    state.record("sig", 30, exhausted=False)
    reloaded = StateStore(tmp_path / "state.json")
    reloaded.load()
    assert reloaded.next_start("sig") == 30
    assert reloaded.next_start("other", default=10) == 10


def test_a_cursor_field_of_the_wrong_type_is_read_as_its_type_or_as_absent(tmp_path: Path) -> None:
    # The state file is JSON a person opens and edits. "10" is a cursor; "no" is not a flag, and
    # reading it as one (text is true) would report the target finished and skip it entirely.
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "q=x&lang=en": {"next_start": "10", "exhausted": "no", "updated_at": 7},
                "q=y&lang=en": {"next_start": "soon", "exhausted": True},
                "q=z&lang=en": "not an object",
            }
        ),
        encoding="utf-8",
    )
    state = StateStore(path)
    state.load()
    assert state.next_start("q=x&lang=en") == 10
    assert state.next_start("q=y&lang=en") == 0
    assert state.next_start("q=z&lang=en", default=-1) == -1, "an entry that is not one is dropped"
    entries = {entry.signature: entry for entry in state.entries()}
    assert entries["q=x&lang=en"].exhausted is False, "a target read as finished is never crawled"
    assert entries["q=x&lang=en"].updated_at == ""
    assert state.repaired == 3


def test_a_state_file_that_is_not_readable_stops_the_run(tmp_path: Path) -> None:
    # Reading it as empty would re-crawl every target in it, and only the requests spent would
    # say so. Both spellings of unreadable are the same answer.
    truncated = tmp_path / "truncated.json"
    truncated.write_text('{"a": ', encoding="utf-8")
    with pytest.raises(CrawlFailure) as broken:
        StateStore(truncated).load()
    assert broken.value.diagnosis.failure is Failure.STATE_UNREADABLE
    assert str(truncated) in broken.value.diagnosis.what
    assert any("move" in step for step in broken.value.diagnosis.next_steps)

    listed = tmp_path / "listed.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(CrawlFailure) as wrong_shape:
        StateStore(listed).load()
    assert "holds a list" in wrong_shape.value.diagnosis.detail


def test_the_profile_file_is_named_after_the_records_file() -> None:
    assert profiles_beside(Path("out/gnn.jsonl")) == Path("out/gnn.profiles.jsonl")
    assert profiles_beside(Path("gnn")) == Path("gnn.profiles.jsonl")
    assert profiles_beside(Path("out/a.jsonl")) != profiles_beside(Path("out/b.jsonl"))


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere")
def test_a_path_is_checked_without_creating_anything(tmp_path: Path) -> None:
    fine = tmp_path / "records.jsonl"
    assert unwritable(fine) == ""
    assert not fine.exists(), "checking is not creating"

    # A run creates missing parents itself, so a path below one is writable in advance.
    deep = tmp_path / "a" / "b" / "records.jsonl"
    assert unwritable(deep) == ""
    assert nearest_existing(deep.parent) == tmp_path
    assert not deep.parent.exists()

    as_directory = tmp_path / "refs.bib"
    as_directory.mkdir()
    assert "is a directory" in unwritable(as_directory)
    assert unwritable(as_directory, kind="dir") == "", "as a directory it is fine"

    # The mirror mistake: a flag that wants a directory pointed at a file, and a file asked for
    # below one. Nothing can be created under a file, however writable the directory above it is.
    as_file = tmp_path / "profile"
    as_file.write_text("", encoding="utf-8")
    assert "is a file, and this run needs a directory there" in unwritable(as_file, kind="dir")
    assert f"{as_file} is a file, so" in unwritable(as_file / "deep" / "records.jsonl")
    assert "and this run needs a directory there" in unwritable(as_file / "records.jsonl")

    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o500)
    try:
        reason = unwritable(closed / "records.jsonl")
    finally:
        closed.chmod(0o700)
    assert "cannot be written to" in reason and str(closed) in reason
    assert not (closed / PROBE_NAME).exists(), "the probe cleans up after itself"
