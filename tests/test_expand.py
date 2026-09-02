"""Citation-graph expansion: level selection, bounds and CLI wiring."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import json  # noqa: E402
from collections.abc import Iterator  # noqa: E402

from scholar_crawler.cli import _limits_of, build_parser, filter_template  # noqa: E402
from scholar_crawler.expand import FollowPolicy, next_level  # noqa: E402
from scholar_crawler.models import PageResult, ScholarResult, SearchRequest  # noqa: E402
from scholar_crawler.run import follow_citations  # noqa: E402
from scholar_crawler.storage import ResultSink, StateStore  # noqa: E402

TEMPLATE = SearchRequest(cites="0", year_low=2020, language="de", review_only=True)


def _record(cluster_id: str, citations: int | None, cites_id: str | None = "9" * 6) -> ScholarResult:
    url = f"https://scholar.google.com/scholar?cites={cites_id}&as_sdt=5" if cites_id else None
    return ScholarResult(
        cluster_id=cluster_id,
        position=1,
        title=f"record {cluster_id}",
        link=None,
        resource_link=None,
        resource_type=None,
        byline="",
        authors=None,
        venue=None,
        year=None,
        snippet="",
        cited_by_count=citations,
        cited_by_url=url,
        versions_count=None,
        versions_url=None,
        related_url=None,
        citation_only=False,
    )


def test_most_cited_records_are_expanded_first() -> None:
    records = [
        _record("a", 5, "111"),
        _record("b", 500, "222"),
        _record("c", 50, "333"),
    ]
    requests = next_level(records, TEMPLATE, FollowPolicy(depth=1, breadth=2), set())
    assert [request.cites for request in requests] == ["222", "333"]


def test_filters_are_copied_and_the_query_is_dropped() -> None:
    request = next_level([_record("a", 1, "111")], TEMPLATE, FollowPolicy(depth=1), set())[0]
    assert (request.query, request.cluster) == ("", None)
    assert request.cites == "111"
    assert (request.year_low, request.language, request.review_only) == (2020, "de", True)
    assert request.label == "cites:111"


def test_visited_ids_are_not_requested_twice() -> None:
    visited: set[str] = set()
    records = [_record("a", 9, "111"), _record("b", 8, "222")]
    first = next_level(records, TEMPLATE, FollowPolicy(depth=2, breadth=5), visited)
    assert [request.cites for request in first] == ["111", "222"]
    assert next_level(records, TEMPLATE, FollowPolicy(depth=2, breadth=5), visited) == []
    assert visited == {"111", "222"}


def test_records_below_the_citation_floor_are_skipped() -> None:
    records = [_record("a", 3, "111"), _record("b", 30, "222")]
    policy = FollowPolicy(depth=1, breadth=5, min_citations=10)
    assert [request.cites for request in next_level(records, TEMPLATE, policy, set())] == ["222"]


def test_records_without_a_citing_works_link_are_skipped() -> None:
    records = [_record("a", 100, cites_id=None), _record("b", None, "222")]
    requests = next_level(records, TEMPLATE, FollowPolicy(depth=1), set())
    assert [request.cites for request in requests] == ["222"]


def test_an_unparsable_citing_works_link_is_ignored() -> None:
    broken = _record("a", 10, "111")
    broken.cited_by_url = "https://scholar.google.com/scholar?q=citations+without+an+id"
    assert next_level([broken], TEMPLATE, FollowPolicy(depth=1), set()) == []


def test_worst_case_listing_estimate() -> None:
    assert FollowPolicy(depth=0).estimate(3) == 3
    assert FollowPolicy(depth=1, breadth=5).estimate(1) == 6
    assert FollowPolicy(depth=2, breadth=5).estimate(1) == 31
    assert FollowPolicy(depth=2, breadth=2).estimate(2) == 2 + 4 + 8


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"depth": -1}, "depth must not be negative"),
        ({"breadth": 0}, "breadth must be at least 1"),
        ({"min_citations": -5}, "citation floor must not be negative"),
    ],
)
def test_invalid_policies_are_rejected(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        FollowPolicy(**kwargs)


def test_expansion_is_off_by_default_on_the_command_line() -> None:
    args = build_parser().parse_args(["-q", "x"])
    policy = FollowPolicy(
        depth=args.follow_cites,
        breadth=args.follow_breadth,
        min_citations=args.follow_min_citations,
    )
    assert policy.enabled is False
    assert (policy.breadth, policy.min_citations) == (5, 0)


def test_follow_flags_and_filter_template_come_from_arguments() -> None:
    args = build_parser().parse_args(
        [
            "-q",
            "x",
            "--follow-cites",
            "2",
            "--follow-breadth",
            "3",
            "--follow-min-citations",
            "25",
            "--year-from",
            "2019",
            "--no-patents",
        ]
    )
    policy = FollowPolicy(
        depth=args.follow_cites,
        breadth=args.follow_breadth,
        min_citations=args.follow_min_citations,
    )
    assert (policy.depth, policy.breadth, policy.min_citations) == (2, 3, 25)
    template = filter_template(args)
    assert template.year_low == 2019
    assert template.include_patents is False


class _StubCrawler:
    """Serves canned records per cites id and records the listings requested."""

    def __init__(self, pages: dict[str, list[ScholarResult]]) -> None:
        self._pages = pages
        self.requested: list[str] = []

    def search(
        self, request: SearchRequest, *, max_pages: int, start: int, max_results: int | None
    ) -> Iterator[PageResult]:
        self.requested.append(request.cites or "")
        yield PageResult(
            start=0,
            results=self._pages.get(request.cites or "", []),
            total_estimate=None,
            has_next=False,
        )


def test_levels_expand_outward_until_nothing_is_left(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "-q",
            "seed",
            "--follow-cites",
            "3",
            "--follow-breadth",
            "1",
            "-o",
            str(tmp_path / "out.jsonl"),
            "--state",
            str(tmp_path / "state.json"),
        ]
    )
    seed = _record("seed", 100, "111")
    level_one = _record("first", 50, "222")
    crawler = _StubCrawler({"111": [level_one], "222": [_record("second", 1, None)]})
    sink = ResultSink(args.out)
    sink.open()
    state = StateStore(args.state)
    follow_citations(
        crawler,  # type: ignore[arg-type]
        [seed],
        _limits_of(args),
        FollowPolicy(depth=args.follow_cites, breadth=args.follow_breadth),
        filter_template(args),
        sink,
        state,
        None,
    )
    sink.close()

    assert crawler.requested == ["111", "222"]
    records = [
        json.loads(line)
        for line in args.out.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [(record["cluster_id"], record["extra"]["follow_depth"]) for record in records] == [
        ("first", 1),
        ("second", 2),
    ]


def test_a_disabled_policy_requests_nothing(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        ["-q", "seed", "-o", str(tmp_path / "out.jsonl"), "--state", str(tmp_path / "state.json")]
    )
    crawler = _StubCrawler({})
    follow_citations(
        crawler,  # type: ignore[arg-type]
        [_record("seed", 10, "111")],
        _limits_of(args),
        FollowPolicy(depth=0),
        filter_template(args),
        ResultSink(args.out),
        StateStore(args.state),
        None,
    )
    assert crawler.requested == []
