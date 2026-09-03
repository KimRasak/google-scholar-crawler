"""The printed recipes: every command must still parse and mean what it says."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import digest  # noqa: E402
from scholar_crawler.cli import build_parser, build_targets, main  # noqa: E402
from scholar_crawler.config import resolve_settings  # noqa: E402
from scholar_crawler.recipes import RECIPES, getting_started, render  # noqa: E402

PROGRAMS = ("scholar-crawler", "scholar-digest")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A directory holding the files the recipes refer to, including the shipped example settings."""
    (tmp_path / "queries.txt").write_text("graph attention networks\n", encoding="utf-8")
    (tmp_path / "out").mkdir()
    example = Path(__file__).resolve().parents[1] / "scholar.toml.example"
    (tmp_path / "scholar.toml").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "out" / "collected.jsonl").write_text(
        '{"title": "A paper", "cluster_id": "AAA", "query": "x"}\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_first_recipe_collects_papers() -> None:
    first = RECIPES[0]
    argv = shlex.split(first.command)[1:]
    args = build_parser().parse_args(argv)
    assert args.query and args.out, f"the first recipe must collect, not diagnose: {first.command}"
    assert not (args.doctor or args.dry_run or args.self_check or args.explain)


def test_every_recipe_names_a_real_command_and_parses(workspace: Path) -> None:
    assert RECIPES, "the recipe list must not be empty"
    for recipe in RECIPES:
        argv = shlex.split(recipe.command)
        assert argv[0] in PROGRAMS, recipe.command
        rest = [
            # the shell expands the glob before the program sees it
            *(("out/collected.jsonl",) if argv[1:2] == ["out/*.jsonl"] else ()),
            *(argument for argument in argv[1:] if argument != "out/*.jsonl"),
        ]
        parser = build_parser() if argv[0] == "scholar-crawler" else digest.build_parser()
        parsed = parser.parse_args(rest)  # argparse exits on an unknown or malformed flag
        assert parsed is not None


def test_every_crawler_recipe_describes_a_buildable_run(workspace: Path) -> None:
    for recipe in RECIPES:
        argv = shlex.split(recipe.command)
        if argv[0] != "scholar-crawler":
            continue
        args = build_parser().parse_args(argv[1:])
        if args.doctor or args.self_check or args.rehearse_handoff:
            continue  # these modes carry no target by design
        # A recipe may keep its targets in a settings file, exactly as a run does.
        resolve_settings(args, build_parser(), argv[1:])
        listings, authors = build_targets(args)
        assert listings or authors, recipe.command


def test_the_doctor_recipe_runs_as_written(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doctor = [recipe for recipe in RECIPES if "--doctor" in recipe.command]
    assert doctor, "keep a recipe that checks the machine before anything is requested"
    for recipe in doctor:
        assert main(shlex.split(recipe.command)[1:]) == 0
        printed = capsys.readouterr().out
        assert "[doctor] + python" in printed
        assert "[doctor] + browser" in printed


def test_the_explain_recipe_runs_as_written(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    explained = [recipe for recipe in RECIPES if "--explain" in recipe.command]
    assert explained, "keep a recipe that reads a command back before it runs"
    for recipe in explained:
        assert main(shlex.split(recipe.command)[1:]) == 0
        printed = capsys.readouterr().out
        assert "[explain] crawling" in printed


def test_the_dry_run_recipe_runs_as_written(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dry = [recipe for recipe in RECIPES if "--dry-run" in recipe.command]
    assert dry, "keep a recipe that costs a run without sending anything"
    for recipe in dry:
        assert main(shlex.split(recipe.command)[1:]) == 0
        printed = capsys.readouterr().out
        assert "[plan] total:" in printed


def test_recipes_read_as_purpose_command_note() -> None:
    lines = render()
    assert len(lines) == 3 * len(RECIPES)
    assert lines[0].startswith("1. ")
    assert lines[1].startswith("   $ scholar-")
    assert lines[2].startswith("     ")
    assert len(getting_started(3)) == 9


def test_the_recipe_list_is_printable_and_stops(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--recipes"]) == 0
    printed = capsys.readouterr().out
    assert printed.count("$ scholar-") == len(RECIPES)
    assert "--self-check" in printed


def test_a_run_with_no_arguments_points_at_the_recipes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["scholar-crawler"])
    assert main([]) == 1
    captured = capsys.readouterr()
    assert "provide at least one --query" in captured.err
    assert "--recipes" in captured.err
    # The way out of this error is a command that collects, not another diagnostic.
    assert "-q \"graph attention networks\"" in captured.err
    assert captured.err.index("Collect one topic") < captured.err.index("--doctor")


def test_a_usage_error_with_arguments_stays_terse(capsys: pytest.CaptureFixture[str]) -> None:
    # A user who passed something wrong wants the error, not a tutorial.
    assert main(["--follow-cites", "1"]) == 1
    captured = capsys.readouterr()
    assert "provide at least one --query" in captured.err
    assert "$ scholar-crawler" not in captured.err
