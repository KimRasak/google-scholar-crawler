"""Incremental output and resume state."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.parser import parse_result_page  # noqa: E402
from scholar_crawler.storage import ResultSink, StateStore, profiles_beside  # noqa: E402
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


def test_the_profile_file_is_named_after_the_records_file() -> None:
    assert profiles_beside(Path("out/gnn.jsonl")) == Path("out/gnn.profiles.jsonl")
    assert profiles_beside(Path("gnn")) == Path("gnn.profiles.jsonl")
    assert profiles_beside(Path("out/a.jsonl")) != profiles_beside(Path("out/b.jsonl"))
