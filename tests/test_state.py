"""Resume-state inspection: readable signatures, stored entries and the CLI paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections.abc import Iterator  # noqa: E402

from scholar_crawler.cli import _limits_of, build_parser, main  # noqa: E402
from scholar_crawler.models import (  # noqa: E402
    AuthorRequest,
    PageResult,
    ScholarResult,
    SearchRequest,
    describe_signature,
    parse_signature,
)
from scholar_crawler.run import crawl_listing, report_ignored_progress  # noqa: E402
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
    assert describe_signature("author=x|lang=en") == "author=x|lang=en"  # a field short
    assert describe_signature("q|a=|b=|c=|d=|e=|f=|g=|h=|i=") == "q|a=|b=|c=|d=|e=|f=|g=|h=|i="
    assert describe_signature("|cites=|cluster=|lo=|hi=|lang=|date=0|cit=1|pat=1|rev=0") == (
        "|cites=|cluster=|lo=|hi=|lang=|date=0|cit=1|pat=1|rev=0"  # no entry point to name
    )


REQUESTS = [
    SearchRequest(query="deep learning"),
    SearchRequest(query="graph | attention networks", language="en"),  # the join character itself
    SearchRequest(query="", cites="123", year_low=2020, sort_by_date=True),
    SearchRequest(cluster="9", language="zh-CN", review_only=True, include_patents=False),
    SearchRequest(query="x", cites="7", year_low=2010, year_high=2015, include_citations=False),
    AuthorRequest(user_id="A" * 12, sort_by_year=True),
    AuthorRequest(user_id="B" * 12, language="de"),
]


@pytest.mark.parametrize("request_", REQUESTS, ids=lambda item: item.label)
def test_a_stored_signature_still_holds_the_request_that_wrote_it(
    request_: SearchRequest | AuthorRequest,
) -> None:
    # The description of a live target and of a stored one come from one place, which only holds
    # if a signature can be read back exactly — including a query containing the "|" that joins
    # the fields, which used to be silently cut at the first one.
    signature = request_.signature()
    restored = parse_signature(signature)
    assert restored == request_
    assert restored.signature() == signature
    assert describe_signature(signature) == request_.describe()


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
    assert "dropped 1 target" in printed
    assert "crawled from the start again" in printed
    assert main(["--forget", "cites=123456", "--state", path]) == 0
    missed = capsys.readouterr().out
    assert "no stored target matches" in missed
    # A miss is a pattern typed from memory, so the printed alternatives are the answer.
    assert "stored: attention is all you need [en]" in missed


def test_a_target_can_be_forgotten_by_the_name_show_state_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # What --show-state prints used to match nothing here: it names targets one way and this
    # matched the stored signature the other way, so a copied name always missed.
    _store(tmp_path)
    path = str(tmp_path / "state.json")
    assert main(["--show-state", "--state", path]) == 0
    shown = capsys.readouterr().out
    assert "attention is all you need [en]" in shown

    assert main(["--forget", "attention is all you need [en]", "--state", path]) == 0
    printed = capsys.readouterr().out
    assert "dropped 1 target" in printed
    assert main(["--show-state", "--state", path]) == 0
    assert "attention is all you need" not in capsys.readouterr().out


def test_show_state_hands_back_the_command_that_continues_each_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.record(SearchRequest(query="graph | attention", language="de", year_low=2020).signature(), 10)
    assert main(["--show-state", "--state", str(tmp_path / "state.json")]) == 0
    printed = capsys.readouterr().out

    # The query keeps the character the signature is joined with, and quoting survives it.
    assert "$ scholar-crawler -q 'graph | attention' --year-from 2020 --lang de --resume" in printed
    assert "$ scholar-crawler --author AAAAAAAAAAAA --resume" in printed
    # A finished target has nothing to continue, so it gets a line and no command.
    assert "cites:123456 [en] — done after 50 records" in printed
    assert "--cites 123456" not in printed
    # The default language and the default state path are not worth spelling out.
    assert "--lang en" not in printed
    assert "--state" in printed  # this state file is not the default one


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
    limits = _limits_of(args)
    crawl_listing(_CappedCrawler(), SearchRequest(query="capped"), limits, sink, state, None)  # type: ignore[arg-type]
    sink.close()
    return state


def test_a_record_cap_does_not_mark_a_target_finished(tmp_path: Path) -> None:
    # --max-results stopping the run is our decision; Scholar still has pages to serve,
    # so the stored progress must stay resumable instead of reading as finished.
    entry = _run_capped_listing(tmp_path).entries()[0]
    assert (entry.exhausted, entry.next_start) == (False, 2)
    assert "next offset 2" in entry.describe()


def test_a_rerun_without_resume_is_told_what_it_will_redo(tmp_path: Path) -> None:
    # Repeating a command without --resume refetches offset 0 and writes nothing new, spending
    # the one scarce resource: a request Scholar could answer with a challenge.
    store = _store(tmp_path)
    listing = SearchRequest(query="attention is all you need", language="en")
    fresh = SearchRequest(query="never crawled", language="en")
    targets = [(listing.describe(), listing.signature()), (fresh.describe(), fresh.signature())]

    lines = report_ignored_progress(store, targets, resume=False)
    assert len(lines) == 1, "only targets with recorded progress are worth a line"
    # Named as the resume file names it, so it can be taken straight to --forget.
    assert "'attention is all you need [en]' already reached offset 30" in lines[0]
    assert "--resume" in lines[0] and str(store.path) in lines[0]
    assert report_ignored_progress(store, targets, resume=True) == []


def test_the_rerun_notice_reaches_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state.json"
    StateStore(state).record(SearchRequest(query="graph neural networks", language="en").signature(), 20)
    exit_code = main(
        ["-q", "graph neural networks", "--dry-run", "--state", str(state), "-o", str(tmp_path / "o.jsonl")]
    )
    printed = capsys.readouterr().out
    assert exit_code == 0
    assert "[state] 'graph neural networks [en]' already reached offset 20" in printed
