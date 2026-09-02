"""Run planning: page-load arithmetic, duration estimates and the ``--dry-run`` path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.crawler import Pacing  # noqa: E402
from scholar_crawler.expand import FollowPolicy  # noqa: E402
from scholar_crawler.models import AuthorRequest, SearchRequest  # noqa: E402
from scholar_crawler.plan import LOAD_SECONDS, pages_needed, plan_run  # noqa: E402

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
