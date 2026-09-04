"""Command-line wiring: argument parsing into requests, and early failure paths."""

from __future__ import annotations

import json
import os
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


def test_a_list_file_that_names_nothing_says_so_instead_of_asking_for_a_target(
    tmp_path: Path,
) -> None:
    # "provide at least one --query …" was the message for a file that was provided, which sent
    # the reader looking at their command line instead of at the file.
    empty = tmp_path / "clusters.txt"
    empty.write_text("\n\n   \n# only a comment\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"--clusters-file .* names no target"):
        build_targets(_args(["--clusters-file", str(empty)]))  # type: ignore[arg-type]

    queries = tmp_path / "queries.txt"
    queries.write_text("# nothing but this\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"--queries-file .* names no target"):
        build_targets(_args(["--queries-file", str(queries)]))  # type: ignore[arg-type]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root writes anywhere")
def test_a_path_the_run_cannot_write_stops_it_before_the_first_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # --dry-run exists to catch this kind of mistake before requests are spent, so it checks the
    # same paths a real run would open.
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o500)
    shared = [
        "-q",
        "x",
        "-p",
        "1",
        "--state",
        str(tmp_path / "state.json"),
        "--challenge-log",
        str(tmp_path / "challenges.jsonl"),
        "--dry-run",
        "--json",
    ]
    try:
        assert main(["-o", str(closed / "results.jsonl"), *shared]) == 1
        first = json.loads(capsys.readouterr().out)

        as_directory = tmp_path / "refs.bib"
        as_directory.mkdir()
        assert main(["-o", str(tmp_path / "results.jsonl"), "--bibtex", str(as_directory), *shared]) == 1
        second = json.loads(capsys.readouterr().out)
    finally:
        closed.chmod(0o700)

    assert first["error"]["kind"] == "path_unwritable"
    assert str(closed) in first["error"]["message"]
    assert first["error"]["next_steps"][0] == "point --out at a file this user can write"
    assert "plan" not in first, "a run that cannot write is not costed"

    assert second["error"]["kind"] == "path_unwritable"
    assert "is a directory" in second["error"]["message"]
    assert second["error"]["next_steps"][0] == "point --bibtex at a file this user can write"


def test_the_doctor_checks_the_paths_this_command_would_write(tmp_path: Path) -> None:
    # A report that checks the defaults while the run writes somewhere else is worse than no
    # report: it says "ready" about paths the run never touches.
    from scholar_crawler.cli import _written_paths

    args = _args(
        [
            "-q",
            "x",
            "-o",
            str(tmp_path / "a" / "records.jsonl"),
            "--state",
            str(tmp_path / "b" / "state.json"),
            "--challenge-log",
            str(tmp_path / "c" / "challenges.jsonl"),
            "--profile",
            str(tmp_path / "d"),
            "--bibtex",
            str(tmp_path / "e" / "refs.bib"),
            "--dump-html",
            str(tmp_path / "f"),
        ]
    )
    written = _written_paths(args)  # type: ignore[arg-type]
    assert [flag for flag, _path, _kind in written] == [
        "--out",
        "--bibtex",
        "--state",
        "--challenge-log",
        "--dump-html",
        "--profile",
    ]
    assert [kind for _flag, _path, kind in written] == ["file", "file", "file", "file", "dir", "dir"]
    assert all(str(tmp_path) in str(path) for _flag, path, _kind in written)

    # The optional exports are absent unless asked for, so a plain run is not told to check them.
    plain = _written_paths(_args(["-q", "x"]))  # type: ignore[arg-type]
    assert [flag for flag, _path, _kind in plain] == ["--out", "--state", "--challenge-log", "--profile"]


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
