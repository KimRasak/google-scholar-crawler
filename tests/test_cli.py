"""Command-line wiring: argument parsing into requests, and early failure paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.browser import locale_for, timezone_for  # noqa: E402
from scholar_crawler.cli import build_parser, build_targets, main  # noqa: E402


def _args(argv: list[str]) -> object:
    return build_parser().parse_args(argv)


def test_queries_file_and_flags_are_combined(tmp_path: Path) -> None:
    listing = tmp_path / "queries.txt"
    listing.write_text("# comment\nfrom file\n\n  spaced  \n", encoding="utf-8")
    requests, authors = build_targets(_args(["-q", "flag one", "--queries-file", str(listing)]))  # type: ignore[arg-type]
    assert [request.query for request in requests] == ["flag one", "from file", "spaced"]
    assert authors == []


def test_filters_apply_to_every_request() -> None:
    requests, _authors = build_targets(  # type: ignore[arg-type]
        _args(["-q", "a", "-q", "b", "--year-from", "2020", "--no-patents", "--lang", "zh-CN"])
    )
    assert len(requests) == 2
    for request in requests:
        assert request.year_low == 2020
        assert request.include_patents is False
        assert request.language == "zh-CN"


def test_cites_and_cluster_accept_urls_from_collected_records() -> None:
    requests, _authors = build_targets(  # type: ignore[arg-type]
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
        build_targets(_args([]))  # type: ignore[arg-type]


def test_unparsable_cites_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="no Scholar cites/cluster id"):
        build_targets(_args(["--cites", "not-an-id"]))  # type: ignore[arg-type]


def test_author_profiles_are_separate_targets() -> None:
    listings, authors = build_targets(  # type: ignore[arg-type]
        _args(
            [
                "-q",
                "keyword",
                "--author",
                "https://scholar.google.com/citations?user=AAAAAAAAAAAA&hl=en",
                "--author",
                "BBBBBBBBBBBB",
                "--sort-by-date",
                "--lang",
                "de",
            ]
        )
    )
    assert [listing.query for listing in listings] == ["keyword"]
    assert [author.user_id for author in authors] == ["AAAAAAAAAAAA", "BBBBBBBBBBBB"]
    assert all(author.sort_by_year and author.language == "de" for author in authors)


def test_unparsable_author_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="no Scholar profile id"):
        build_targets(_args(["--author", "Ada Lovelace"]))  # type: ignore[arg-type]


def test_main_reports_usage_errors_without_touching_the_browser(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "provide at least one" in capsys.readouterr().err


def test_main_reports_invalid_pacing(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-q", "x", "--min-delay", "20", "--max-delay", "5"]) == 1
    assert "exceeds max_delay" in capsys.readouterr().err


def test_the_browser_locale_follows_the_interface_language() -> None:
    # One fact, one home: a window asking Scholar for German pages while reporting
    # Accept-Language: en-US describes a browser nobody has.
    assert locale_for("en") == "en-US", "plain en is not what a real browser sends"
    assert locale_for("zh-CN") == "zh-CN"
    assert locale_for("de") == "de"


def test_the_timezone_follows_the_language_unless_one_is_given() -> None:
    # The zone is part of the same fact as the locale: it has to agree with the language.
    assert timezone_for("de") == "Europe/Berlin"
    assert timezone_for("pt-BR") == "America/Sao_Paulo"
    # A regional tag is read whole before its base language.
    assert timezone_for("zh-CN") == "Asia/Shanghai"
    assert timezone_for("zh-TW") == "Asia/Taipei"
    assert timezone_for("en-GB") == "Europe/London"
    # A language the table does not name gets a zone that still agrees with nothing in
    # particular, rather than quietly claiming California.
    assert timezone_for("sw") == "UTC"
    assert timezone_for("EN") == "America/Los_Angeles"
