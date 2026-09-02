"""Command-line wiring: argument parsing into requests, and early failure paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser, build_requests, main  # noqa: E402


def _args(argv: list[str]) -> object:
    return build_parser().parse_args(argv)


def test_queries_file_and_flags_are_combined(tmp_path: Path) -> None:
    listing = tmp_path / "queries.txt"
    listing.write_text("# comment\nfrom file\n\n  spaced  \n", encoding="utf-8")
    requests = build_requests(_args(["-q", "flag one", "--queries-file", str(listing)]))  # type: ignore[arg-type]
    assert [request.query for request in requests] == ["flag one", "from file", "spaced"]


def test_filters_apply_to_every_request() -> None:
    requests = build_requests(  # type: ignore[arg-type]
        _args(["-q", "a", "-q", "b", "--year-from", "2020", "--no-patents", "--lang", "zh-CN"])
    )
    assert len(requests) == 2
    for request in requests:
        assert request.year_low == 2020
        assert request.include_patents is False
        assert request.language == "zh-CN"


def test_cites_and_cluster_accept_urls_from_collected_records() -> None:
    requests = build_requests(  # type: ignore[arg-type]
        _args(
            [
                "--cites",
                "https://scholar.google.com/scholar?cites=111&as_sdt=2005",
                "--cluster",
                "222",
            ]
        )
    )
    assert [(request.cites, request.cluster) for request in requests] == [("111", None), (None, "222")]


def test_no_entry_point_is_rejected() -> None:
    with pytest.raises(ValueError, match="provide at least one"):
        build_requests(_args([]))  # type: ignore[arg-type]


def test_unparsable_cites_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="no Scholar cites/cluster id"):
        build_requests(_args(["--cites", "not-an-id"]))  # type: ignore[arg-type]


def test_main_reports_usage_errors_without_touching_the_browser(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "provide at least one" in capsys.readouterr().err


def test_main_reports_invalid_pacing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-q", "x", "--min-delay", "20", "--max-delay", "5"]) == 1
    assert "exceeds max_delay" in capsys.readouterr().err
