"""Resume-state inspection: readable signatures, stored entries and the CLI paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections.abc import Iterator  # noqa: E402

from scholar_crawler.cli import _crawl_listing, build_parser, main  # noqa: E402
from scholar_crawler.models import (  # noqa: E402
    AuthorRequest,
    PageResult,
    ScholarResult,
    SearchRequest,
    describe_signature,
)
from scholar_crawler.storage import ResultSink, StateEntry, StateStore  # noqa: E402


def _store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.json")
    store.record(SearchRequest(query="attention is all you need", language="en").signature(), 30)
    store.record(SearchRequest(cites="123456", language="en").signature(), 50, exhausted=True)
    store.record(AuthorRequest(user_id="A" * 12, language="en").signature(), 100)
    return store


def test_signatures_read_back_as_their_targets() -> None:
    assert describe_signature(SearchRequest(query="deep learning").signature()) == "deep learning"
    assert (
        describe_signature(SearchRequest(cites="123", year_low=2020, sort_by_date=True).signature())
        == "cites:123 [2020, by date]"
    )
    assert (
        describe_signature(
            SearchRequest(cluster="9", language="zh-CN", review_only=True, include_patents=False).signature()
        )
        == "cluster:9 [zh-CN, no patents, reviews only]"
    )
    assert (
        describe_signature(SearchRequest(query="x", year_low=2010, year_high=2015).signature())
        == "x [2010-2015]"
    )
    assert describe_signature(AuthorRequest(user_id="A" * 12, sort_by_year=True).signature()) == (
        "author:AAAAAAAAAAAA [by year]"
    )


def test_an_unrecognised_signature_is_shown_as_is() -> None:
    assert describe_signature("something|else") == "something|else"


def test_recorded_entries_carry_a_timestamp(tmp_path: Path) -> None:
    entries = _store(tmp_path).entries()
    assert len(entries) == 3
    assert all(entry.updated_at.endswith("+00:00") for entry in entries)
    stored = json.loads((tmp_path / "state.json").read_text())
    assert all("updated_at" in record for record in stored.values())


def test_entries_survive_a_reload_and_report_progress(tmp_path: Path) -> None:
    _store(tmp_path)
    reloaded = StateStore(tmp_path / "state.json")
    reloaded.load()
    described = [entry.describe() for entry in reloaded.entries()]
    assert any("done after 50 records" in line for line in described)
    assert any("next offset 30" in line for line in described)
    assert all(" UTC" in line for line in described)


def test_state_written_by_an_older_version_still_reads(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"old|sig": {"next_start": 20, "exhausted": False}}), encoding="utf-8")
    store = StateStore(path)
    store.load()
    entry = store.entries()[0]
    assert (entry.next_start, entry.updated_at) == (20, "")
    assert "unknown time" in entry.describe()


def test_forget_removes_only_matching_targets(tmp_path: Path) -> None:
    store = _store(tmp_path)
    removed = store.forget("ATTENTION")  # case-insensitive
    assert [entry.next_start for entry in removed] == [30]
    assert len(store.entries()) == 2
    assert store.forget("nothing here") == []
    assert len(json.loads((tmp_path / "state.json").read_text())) == 2


def test_an_empty_pattern_forgets_everything(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert len(store.forget("")) == 3
    assert store.entries() == []
    assert json.loads((tmp_path / "state.json").read_text()) == {}


def test_show_state_lists_progress(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _store(tmp_path)
    assert main(["--show-state", "--state", str(tmp_path / "state.json")]) == 0
    printed = capsys.readouterr().out
    assert "3 targets" in printed and "(1 finished)" in printed
    assert "attention is all you need [en] — next offset 30" in printed
    assert "author:AAAAAAAAAAAA [en]" in printed


def test_show_state_on_a_fresh_file_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--show-state", "--state", str(tmp_path / "state.json")]) == 0
    assert "nothing stored" in capsys.readouterr().out


def test_forget_from_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _store(tmp_path)
    path = str(tmp_path / "state.json")
    assert main(["--forget", "cites=123456", "--state", path]) == 0
    printed = capsys.readouterr().out
    assert "dropped 1 target(s)" in printed
    assert "crawled from the start again" in printed
    assert main(["--forget", "cites=123456", "--state", path]) == 0
    assert "no stored target matches" in capsys.readouterr().out


def test_state_commands_need_no_crawl_target(tmp_path: Path) -> None:
    # Neither path builds targets or opens a browser, so no query is required.
    assert main(["--show-state", "--state", str(tmp_path / "state.json")]) == 0
    assert main(["--forget", "", "--state", str(tmp_path / "state.json")]) == 0


def test_entry_describes_a_bare_dataclass() -> None:
    entry = StateEntry(
        signature=SearchRequest(query="x").signature(),
        next_start=0,
        exhausted=False,
        updated_at="",
    )
    assert entry.describe() == "x — next offset 0, unknown time"


class _CappedCrawler:
    """Serves one page that Scholar would continue, cut short by the record cap."""

    def search(
        self, request: SearchRequest, *, max_pages: int, start: int, max_results: int | None
    ) -> Iterator[PageResult]:
        results = [
            ScholarResult(
                cluster_id=f"c{index}",
                position=index,
                title=f"paper {index}",
                link=None,
                resource_link=None,
                resource_type=None,
                byline="",
                authors=None,
                venue=None,
                year=None,
                snippet="",
                cited_by_count=None,
                cited_by_url=None,
                versions_count=None,
                versions_url=None,
                related_url=None,
                citation_only=False,
                query=request.label,
            )
            for index in range(max_results or 10)
        ]
        yield PageResult(start=start, results=results, total_estimate=999, has_next=True, truncated=True)


def _run_capped_listing(tmp_path: Path) -> StateStore:
    args = build_parser().parse_args(
        ["-q", "capped", "-p", "1", "-n", "2", "-o", str(tmp_path / "out.jsonl")]
    )
    sink = ResultSink(tmp_path / "out.jsonl")
    sink.open()
    state = StateStore(tmp_path / "state.json")
    _crawl_listing(_CappedCrawler(), SearchRequest(query="capped"), args, sink, state, None)  # type: ignore[arg-type]
    sink.close()
    return state


def test_a_record_cap_does_not_mark_a_target_finished(tmp_path: Path) -> None:
    # --max-results stopping the run is our decision; Scholar still has pages to serve,
    # so the stored progress must stay resumable instead of reading as finished.
    entry = _run_capped_listing(tmp_path).entries()[0]
    assert (entry.exhausted, entry.next_start) == (False, 2)
    assert "next offset 2" in entry.describe()
