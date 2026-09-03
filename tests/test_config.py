"""Settings files, and the rule that the command line always wins.

A file that quietly loses to a default, or quietly beats a flag, would make every later
question about a run ("why was the delay 8 seconds?") unanswerable. These tests pin the
precedence and every refusal, because a settings file is read before any request goes out and
that is the cheapest place to catch a mistake.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser, main  # noqa: E402
from scholar_crawler.config import (  # noqa: E402
    ConfigError,
    Origin,
    Sources,
    apply_settings,
    given_on_command_line,
    read_settings,
    resolve_settings,
)


def _write(tmp_path: Path, body: str, name: str = "scholar.toml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _settings(tmp_path: Path, body: str) -> dict[str, object]:
    return read_settings(_write(tmp_path, body), build_parser())


def _resolved(tmp_path: Path, body: str, argv: list[str]) -> tuple[argparse.Namespace, Sources]:
    path = _write(tmp_path, body)
    full = ["--config", str(path), *argv]
    args = build_parser().parse_args(full)
    return args, resolve_settings(args, build_parser(), full)


def test_a_setting_reads_the_same_at_the_top_level_or_in_a_table(tmp_path: Path) -> None:
    # Tables organise a file for its reader; they are not namespaces.
    flat = _settings(tmp_path, "min-delay = 8.0\npages = 2\n")
    tabled = _settings(tmp_path, "[pacing]\nmin-delay = 8.0\n\n[paging]\npages = 2\n")
    assert flat == tabled == {"min_delay": 8.0, "pages": 2}


def test_a_key_may_be_spelled_as_the_flag_or_as_the_name(tmp_path: Path) -> None:
    assert _settings(tmp_path, "min-delay = 8.0\n") == _settings(tmp_path, "min_delay = 8.0\n")
    assert _settings(tmp_path, '"--min-delay" = 8.0\n') == {"min_delay": 8.0}


def test_a_path_setting_becomes_a_path(tmp_path: Path) -> None:
    settings = _settings(tmp_path, 'out = "out/gnn.jsonl"\n')
    assert settings == {"out": Path("out/gnn.jsonl")}


def test_a_repeatable_flag_takes_a_list(tmp_path: Path) -> None:
    settings = _settings(tmp_path, 'query = ["graph attention", "graph transformer"]\n')
    assert settings == {"query": ["graph attention", "graph transformer"]}


def test_a_file_fills_in_what_the_command_line_left_alone(tmp_path: Path) -> None:
    args, sources = _resolved(tmp_path, "min-delay = 8.0\nmax-delay = 20.0\n", ["-q", "x"])
    assert (args.min_delay, args.max_delay) == (8.0, 20.0)
    assert sources.of("min_delay") is Origin.FILE
    assert sources.of("query") is Origin.COMMAND_LINE
    assert sources.of("pages") is Origin.DEFAULT


def test_a_flag_beats_the_file_and_the_run_says_so(tmp_path: Path) -> None:
    args, sources = _resolved(tmp_path, "min-delay = 8.0\npages = 5\n", ["-q", "x", "--pages", "1"])
    assert args.pages == 1
    assert sources.overridden == ("pages",)
    assert sources.of("pages") is Origin.COMMAND_LINE
    assert "pages came from the command line instead" in "\n".join(sources.describe())


def test_a_repeated_flag_replaces_the_file_list_rather_than_extending_it(tmp_path: Path) -> None:
    args, _ = _resolved(tmp_path, 'query = ["from the file"]\n', ["-q", "asked for now"])
    assert args.query == ["asked for now"]


def test_a_file_that_sets_nothing_useful_says_so(tmp_path: Path) -> None:
    _, sources = _resolved(tmp_path, "pages = 3\n", ["-q", "x", "--pages", "3"])
    assert sources.from_file() == []
    assert "pages came from the command line instead" in "\n".join(sources.describe())


def test_an_empty_file_is_allowed_and_reported(tmp_path: Path) -> None:
    _, sources = _resolved(tmp_path, "", ["-q", "x"])
    assert sources.from_file() == []
    assert "the file set nothing this run uses" in "\n".join(sources.describe())


def test_no_config_flag_means_no_file_and_nothing_to_report() -> None:
    argv = ["-q", "x"]
    args = build_parser().parse_args(argv)
    sources = resolve_settings(args, build_parser(), argv)
    assert sources.path is None
    assert sources.describe() == []
    assert sources.summary() is None


def test_the_one_line_summary_counts_both_sides(tmp_path: Path) -> None:
    _, sources = _resolved(tmp_path, "min-delay = 8.0\npages = 5\n", ["-q", "x", "--pages", "1"])
    summary = sources.summary()
    assert summary is not None
    assert summary.startswith("1 setting(s) from ")
    assert summary.endswith("1 overridden by flags")


def test_a_misspelled_setting_names_the_closest_real_one(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting 'min_dely'; did you mean 'min_delay'"):
        _settings(tmp_path, "min_dely = 5.0\n")


def test_a_setting_nobody_could_mean_is_still_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown setting 'zzzz'"):
        _settings(tmp_path, "zzzz = 1\n")


@pytest.mark.parametrize("mode", ["doctor", "recipes", "dry-run", "self-check", "config"])
def test_a_file_may_not_decide_what_the_command_does(tmp_path: Path, mode: str) -> None:
    with pytest.raises(ConfigError, match="stays on the command line"):
        _settings(tmp_path, f'{mode} = true\n')


def test_a_number_written_as_a_string_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'pages' wants a number, not a string"):
        _settings(tmp_path, 'pages = "two"\n')


def test_a_switch_wants_true_or_false(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'headless' wants true or false"):
        _settings(tmp_path, 'headless = "yes"\n')


def test_a_value_where_a_switch_belongs_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'pages' wants a value, not true/false"):
        _settings(tmp_path, "pages = true\n")


def test_a_repeatable_flag_refuses_a_single_value(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'query' wants a list of values"):
        _settings(tmp_path, 'query = "only one"\n')


def test_a_single_valued_flag_refuses_a_list(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'pages' wants a single value"):
        _settings(tmp_path, "pages = [1, 2]\n")


def test_a_setting_outside_the_allowed_choices_is_refused(tmp_path: Path) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--mood", choices=("calm", "brisk"))
    path = _write(tmp_path, 'mood = "furious"\n', "choices.toml")
    with pytest.raises(ConfigError, match="'mood' must be one of calm, brisk"):
        read_settings(path, parser)


def test_a_table_may_not_nest(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"\[pacing.deeper\] nests too deep"):
        _settings(tmp_path, "[pacing.deeper]\nmin-delay = 1.0\n")


def test_the_same_setting_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="'min-delay' is set twice"):
        _settings(tmp_path, "min_delay = 1.0\n\n[pacing]\nmin-delay = 2.0\n")


def test_broken_toml_is_reported_as_such(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid TOML"):
        _settings(tmp_path, "min-delay = \n")


def test_a_missing_file_is_reported_before_anything_is_crawled(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no such settings file"):
        read_settings(tmp_path / "absent.toml", build_parser())


def test_the_command_line_is_read_back_without_its_defaults() -> None:
    given = given_on_command_line(build_parser(), ["-q", "x", "--pages", "2"])
    assert {"query", "pages"} <= given
    assert "min_delay" not in given


def test_applying_settings_reports_every_origin() -> None:
    args = argparse.Namespace(pages=3, min_delay=None)
    sources = apply_settings(args, {"min_delay": 8.0, "pages": 5}, {"pages"}, Path("s.toml"))
    assert args.min_delay == 8.0
    assert args.pages == 3, "an explicit flag is not overwritten"
    assert sources.of("min_delay") is Origin.FILE
    assert sources.of("pages") is Origin.COMMAND_LINE
    assert sources.overridden == ("pages",)


def test_a_run_driven_by_a_file_plans_the_file_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body = 'query = ["graph attention"]\npages = 2\n\n[pacing]\nmin-delay = 9.0\nmax-delay = 21.0\n'
    path = _write(tmp_path, body)
    assert main(["--config", str(path), "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "4 value(s) in effect" in printed, "the mode lists every setting it took"
    assert "max_delay, min_delay, pages, query" in printed
    assert "[config]" not in printed, "the detail replaces the one-line summary"
    assert "graph attention" in printed
    assert "9–21s between requests" in printed


def test_a_broken_file_stops_the_command_with_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, "pages = true\n")
    assert main(["--config", str(path), "-q", "x", "--dry-run"]) == 1
    assert "wants a value, not true/false" in capsys.readouterr().err


def test_explaining_a_run_lists_where_the_settings_came_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path, 'query = ["graph attention"]\npages = 4\n')
    assert main(["--config", str(path), "--pages", "1", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "[explain] settings file" in printed
    assert "pages came from the command line instead" in printed
    assert "[config]" not in printed, "the detail replaces the one-line summary"
