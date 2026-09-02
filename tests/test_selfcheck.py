"""The ``--self-check`` report: which parsed fields a page must still deliver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.parser import parse_result_page  # noqa: E402
from scholar_crawler.selfcheck import check_page, report  # noqa: E402
from tests.fixtures import EMPTY_PAGE_HTML, RESULT_PAGE_HTML  # noqa: E402


def _named(html: str, start: int = 0) -> dict[str, bool]:
    return {check.name: check.ok for check in check_page(parse_result_page(html, start=start))}


def test_a_healthy_page_passes_every_check_but_the_size_floor() -> None:
    checks = _named(RESULT_PAGE_HTML)
    # The trimmed fixture holds three cards, so only the >=5 records floor fails.
    assert checks["results_parsed"] is False
    assert all(ok for name, ok in checks.items() if name != "results_parsed")


def test_a_page_with_no_results_fails_everything() -> None:
    assert not any(_named(EMPTY_PAGE_HTML).values())


def test_missing_fields_are_pinpointed() -> None:
    degraded = RESULT_PAGE_HTML.replace("gs_rs", "gs_rs_renamed").replace("data-cid", "data-xid")
    checks = _named(degraded)
    assert checks["snippets"] is False
    assert checks["card_ids"] is False
    assert checks["titles"] is True
    assert checks["citation_counts"] is True


def test_a_last_page_fails_only_the_pagination_check() -> None:
    last = RESULT_PAGE_HTML.replace("/scholar?start=10&amp;q=transformer", "#")
    checks = _named(last)
    assert checks["pagination"] is False
    assert checks["titles"] is True


def test_report_prints_one_line_per_check_and_a_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    checks = check_page(parse_result_page(RESULT_PAGE_HTML))
    passed = report(checks)
    printed = capsys.readouterr().out
    assert passed is False
    assert printed.count("[check]") == len(checks) + 1
    assert "FAIL" in printed
    assert "results_parsed" in printed.splitlines()[-2] or "failed:" in printed

    healthy = [check for check in checks if check.ok]
    assert report(healthy) is True
    assert f"all {len(healthy)} checks passed" in capsys.readouterr().out
