"""Run planning: page-load arithmetic, duration estimates and the ``--dry-run`` path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import crawler as crawler_module  # noqa: E402
from scholar_crawler.challenge import HumanHandoff  # noqa: E402
from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.crawler import Pacing, ScholarCrawler  # noqa: E402
from scholar_crawler.expand import FollowPolicy  # noqa: E402
from scholar_crawler.models import AuthorRequest, SearchRequest  # noqa: E402
from scholar_crawler.plan import LOAD_SECONDS, pages_needed, plan_run  # noqa: E402
from scholar_crawler.urls import RESULTS_PER_PAGE  # noqa: E402
from tests.fixtures import result_page_html  # noqa: E402
from tests.test_crawler import NO_DELAY, _FakePage  # noqa: E402

HOST = "https://scholar.google.com"
NO_FOLLOW = FollowPolicy()
PACING = Pacing(min_delay=4.0, max_delay=11.0, cooldown_every=10, cooldown_seconds=90.0)


def _plan(
    listings: list[SearchRequest],
    authors: list[AuthorRequest] | None = None,
    *,
    pages: int = 1,
    max_results: int | None = None,
    follow: FollowPolicy = NO_FOLLOW,
    bibtex: bool = False,
):
    return plan_run(
        listings,
        authors or [],
        pages=pages,
        max_results=max_results,
        follow=follow,
        bibtex=bibtex,
        pacing=PACING,
        host=HOST,
    )


def test_the_record_cap_can_shrink_the_page_budget() -> None:
    assert pages_needed(3, None, 10) == 3
    assert pages_needed(3, 25, 10) == 3
    assert pages_needed(3, 15, 10) == 2
    assert pages_needed(3, 4, 10) == 1
    assert pages_needed(3, 0, 10) == 1
    assert pages_needed(2, 250, 100) == 2


def test_a_keyword_run_counts_pages_and_records() -> None:
    plan = _plan([SearchRequest(query="a"), SearchRequest(query="b")], pages=2)
    assert (plan.seed_loads, plan.seed_records) == (4, 40)
    assert (plan.follow_listings, plan.bibtex_loads) == (0, 0)
    assert plan.total_loads == 4
    assert plan.seconds == pytest.approx(3 * 7.5 + 4 * LOAD_SECONDS)
    assert [label for label, _url in plan.targets] == ["a", "b"]
    assert plan.targets[0][1].startswith(f"{HOST}/scholar?")


def test_profiles_are_planned_in_hundreds_not_tens() -> None:
    plan = _plan([], [AuthorRequest(user_id="A" * 12)], pages=2, max_results=150)
    assert (plan.seed_loads, plan.seed_records) == (2, 150)
    assert plan.targets[0][0] == "author:AAAAAAAAAAAA"
    assert "citations?" in plan.targets[0][1]


def test_expansion_and_bibtex_multiply_the_cost() -> None:
    plan = _plan(
        [SearchRequest(query="a")],
        pages=1,
        follow=FollowPolicy(depth=1, breadth=4),
        bibtex=True,
    )
    assert (plan.seed_loads, plan.seed_records) == (1, 10)
    assert (plan.follow_listings, plan.follow_loads, plan.follow_records) == (4, 4, 40)
    # Ten seed records plus forty expanded ones, two loads each.
    assert plan.bibtex_loads == 100
    assert plan.total_loads == 105
    assert plan.total_records == 50


def test_profile_bibtex_costs_one_load_more_per_record() -> None:
    plan = _plan([], [AuthorRequest(user_id="A" * 12)], max_results=5, bibtex=True)
    assert plan.bibtex_loads == 15


def test_cooldowns_and_hours_appear_in_the_estimate() -> None:
    plan = _plan([SearchRequest(query="a")], pages=30)
    assert plan.cooldowns == 3
    rendered = plan.render()
    assert any("30 page loads" in line for line in rendered)
    assert any("cooldowns of 90s" in line for line in rendered)
    assert any(" min at 4-11s" in line for line in rendered)

    long_plan = _plan(
        [SearchRequest(query="a")], pages=5, follow=FollowPolicy(depth=2, breadth=8), bibtex=True
    )
    assert long_plan.total_loads == 7665  # 72 expanded listings x 50 records, exported
    assert any(" h at " in line for line in long_plan.render())


def test_rendered_plan_lists_targets_then_totals() -> None:
    lines = _plan([SearchRequest(cites="123")], bibtex=True).render()
    assert lines[0].startswith("cites:123 -> https://scholar.google.com/scholar?")
    assert lines[1].startswith("seed targets: 1 page loads")
    assert any(line.startswith("bibtex export:") for line in lines)
    assert any(line.startswith("total: up to") for line in lines)


def test_dry_run_prints_a_plan_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.jsonl"
    code = main(
        [
            "-q",
            "graph neural networks",
            "-p",
            "2",
            "--bibtex",
            str(tmp_path / "refs.bib"),
            "-o",
            str(out),
            "--state",
            str(tmp_path / "state.json"),
            "--dry-run",
        ]
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "[plan] graph neural networks -> https://scholar.google.com/scholar?" in printed
    assert "[plan] total: up to 42 page loads for 20 records" in printed
    assert "nothing was requested" in printed
    assert list(tmp_path.iterdir()) == []


def test_dry_run_still_rejects_a_missing_target(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--dry-run"]) == 1
    assert "provide at least one" in capsys.readouterr().err


def _loads_actually_made(
    monkeypatch: pytest.MonkeyPatch, *, pages: int, max_results: int | None, cards: int
) -> int:
    """Run the real crawl loop over canned pages and count the navigations it makes.

    :param monkeypatch: used to silence pacing and challenge detection.
    :param pages: page budget for the listing.
    :param max_results: record cap, or None.
    :param cards: result cards each canned page carries.
    :returns: how many page loads the crawler performed.
    """
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)
    served = [result_page_html(cards, next_start=(index + 1) * cards) for index in range(pages + 2)]
    page = _FakePage(iter(served))
    crawler = ScholarCrawler(page, HumanHandoff(timeout=1.0), NO_DELAY)  # type: ignore[arg-type]
    for _batch in crawler.search(
        SearchRequest(query="x"), max_pages=pages, start=0, max_results=max_results
    ):
        pass
    return len(page.visited)


def test_the_plan_matches_the_loads_the_crawl_loop_really_makes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The estimate is arithmetic, the crawl is a loop; without this they can drift apart.
    for pages, max_results in ((1, None), (3, None), (3, 25), (3, 15), (3, 4)):
        plan = _plan([SearchRequest(query="x")], pages=pages, max_results=max_results)
        actual = _loads_actually_made(
            monkeypatch, pages=pages, max_results=max_results, cards=RESULTS_PER_PAGE
        )
        assert plan.seed_loads == actual, f"pages={pages} max_results={max_results}"


def test_short_plans_report_seconds_not_zero_minutes() -> None:
    # "0 min" is what rounding a 24-second plan used to produce.
    short = _plan([SearchRequest(query="x")], pages=3, max_results=25)
    assert any(line.startswith("estimated 24s at") for line in short.render())
    long = _plan([SearchRequest(query="x")], pages=40)
    assert any("min at" in line for line in long.render())
