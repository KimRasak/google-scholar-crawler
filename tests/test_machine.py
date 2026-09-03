"""The JSON document: what a program gets instead of terminal lines.

An agent calls this tool once and parses stdout. So the promises tested here are that stdout
carries exactly one JSON document and nothing else, that the document names what was collected
and what it cost, and that a stopped run says what stopped it and what to do next.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import digest  # noqa: E402
from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.machine import (  # noqa: E402
    FALLBACK_VERSION,
    KINDS,
    document,
    emit,
    failure,
    version,
)
from scholar_crawler.modes import install_browser  # noqa: E402

KEYS = {"tool", "version", "ok", "exit_code", "counts", "files", "records", "error"}
"""Top-level keys every document carries, whatever the run did."""


def _document(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Parse the document a run printed, proving stdout carries nothing else."""
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, dict)
    return parsed


def _record(key: str, citations: int = 10) -> dict[str, object]:
    return {
        "cluster_id": key,
        "title": f"Work {key}",
        "cited_by_count": citations,
        "year": 2021,
        "query": "graph attention networks",
    }


def _write(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_a_document_always_carries_the_same_top_level_keys() -> None:
    assembled = document(tool="scholar-crawler", exit_code=0, counts={})
    assert set(assembled) == KEYS
    assert assembled["ok"] is True
    assert assembled["records"] == []
    assert assembled["error"] is None


def test_a_non_zero_exit_is_not_ok() -> None:
    assembled = document(
        tool="scholar-digest", exit_code=1, counts={}, error=failure("no_records", "y")
    )
    assert assembled["ok"] is False
    assert assembled["error"] == {"kind": "no_records", "message": "y", "next_steps": []}


def test_a_kind_a_caller_could_not_branch_on_is_refused() -> None:
    # A typo here would reach an agent as an unhandled branch, so it fails at the source.
    with pytest.raises(ValueError, match="unknown failure kind 'oops'"):
        failure("oops", "something happened")


def test_every_documented_kind_exists_and_every_kind_is_documented() -> None:
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    documented = {kind.strip("`") for kind in re.findall(r"`([a-z_]+)`", text)} & set(KINDS)
    assert documented == set(KINDS), f"AGENTS.md is out of step: {documented ^ set(KINDS)}"


def test_only_the_files_a_run_wrote_are_named() -> None:
    assembled = document(
        tool="scholar-crawler",
        exit_code=0,
        counts={},
        files={"records": Path("out/a.jsonl"), "csv": None},
    )
    assert assembled["files"] == {"records": "out/a.jsonl"}


def test_a_document_is_one_line_terminated_object(capsys: pytest.CaptureFixture[str]) -> None:
    emit({"tool": "scholar-crawler", "note": "中文 stays readable"})
    printed = capsys.readouterr().out
    assert printed.endswith("}\n")
    assert "中文" in printed, "escaping non-ASCII would make titles unreadable"
    assert json.loads(printed)["note"].startswith("中文")


def test_a_version_is_reported_even_from_a_plain_checkout() -> None:
    reported = version()
    assert reported == FALLBACK_VERSION or reported[0].isdigit()


def test_the_version_flag_prints_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("scholar-crawler ")
    assert digest.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("scholar-digest ")


def test_a_costed_run_reports_its_price_as_numbers(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json", "--dry-run", "-q", "graph attention", "-p", "3"]) == 0
    parsed = _document(capsys)
    assert parsed["ok"] is True
    assert parsed["plan"]["page_loads"] == 3
    assert parsed["plan"]["records_at_most"] == 30
    assert parsed["plan"]["seconds"] > 0
    assert parsed["plan"]["targets"][0]["label"] == "graph attention"
    assert "scholar.google.com" in parsed["plan"]["targets"][0]["url"]


def test_a_run_with_nothing_to_crawl_fails_in_both_registers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--json"]) == 1
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert parsed["ok"] is False
    assert parsed["error"]["kind"] == "usage"
    assert "provide at least one --query" in captured.err, "the person still gets the reason"


@pytest.mark.parametrize(
    "mode", [["--doctor"], ["--recipes"], ["--show-state"], ["--explain", "-q", "x"], ["--version"]]
)
def test_a_mode_that_json_cannot_describe_is_refused(
    mode: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    # Printing an empty document for --doctor would promise a result that does not exist.
    assert main(["--json", *mode]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["kind"] == "unsupported_mode"
    assert "--json describes a crawl" in captured.err


def test_a_digest_document_carries_the_records_and_the_overview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "one.jsonl", [_record("a", 100), _record("b", 5)])
    assert digest.main([str(path), "--json"]) == 0
    parsed = _document(capsys)
    assert parsed["tool"] == "scholar-digest"
    assert parsed["counts"] == {
        "records": 2,
        "read": 2,
        "files": 1,
        "duplicates": 0,
        "filtered_out": 0,
        "unreadable_lines": 0,
    }
    assert [record["cluster_id"] for record in parsed["records"]] == ["a", "b"]
    assert parsed["overview"]["citations"] == 105
    assert parsed["overview"]["most_cited"][0] == {
        "citations": 100,
        "year": 2021,
        "title": "Work a",
    }
    assert "delta" not in parsed, "a run given nothing to compare against reports no comparison"


def test_a_digest_document_reports_what_changed_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    earlier = _write(tmp_path / "old.jsonl", [_record("a", 100)])
    now = _write(tmp_path / "new.jsonl", [_record("a", 140), _record("b", 3)])
    assert digest.main([str(now), "--since", str(earlier), "--json"]) == 0
    delta = _document(capsys)["delta"]
    assert delta["added"] == ["Work b"]
    assert delta["gone"] == []
    assert delta["citations_gained"] == 40
    assert delta["moved"] == [{"title": "Work a", "before": 100, "after": 140, "change": 40}]


def test_a_digest_document_names_every_file_it_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "one.jsonl", [_record("a")])
    code = digest.main(
        [
            str(path),
            "--json",
            "-o",
            str(tmp_path / "merged.jsonl"),
            "--csv",
            str(tmp_path / "merged.csv"),
            "--bibtex",
            str(tmp_path / "refs.bib"),
        ]
    )
    assert code == 0
    assert set(_document(capsys)["files"]) == {"records", "csv", "bibtex"}


def test_a_digest_refusal_is_a_document_with_a_named_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert digest.main(["--json", str(tmp_path / "absent.jsonl")]) == 1
    parsed = _document(capsys)
    assert parsed["error"]["kind"] == "unreadable_input"
    assert parsed["ok"] is False


def test_every_human_line_leaves_stdout_alone_in_json_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "one.jsonl", [_record("a")])
    assert digest.main([str(path), "--json"]) == 0
    captured = capsys.readouterr()
    assert "[in] 1 records" in captured.err, "progress belongs on stderr when stdout is a document"
    json.loads(captured.out)  # would raise if a single human line had leaked


def test_the_browser_install_runs_playwright_in_this_interpreter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The download has to land in the environment this tool was installed into, which is why
    # it goes through sys.executable rather than a bare 'playwright' on PATH.
    calls: list[list[str]] = []

    def _record_call(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        # The second call is the doctor's path probe, which reports where the browser landed.
        return subprocess.CompletedProcess(command, 0, stdout=sys.executable, stderr="")

    monkeypatch.setattr(subprocess, "run", _record_call)
    assert install_browser() == 0
    assert calls[0] == [sys.executable, "-m", "playwright", "install", "chromium"]
    printed = capsys.readouterr().out
    assert "downloading Chromium" in printed
    assert "run --doctor" in printed


def test_a_failed_browser_install_says_how_to_see_why(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _fail(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 3, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fail)
    assert install_browser() == 1
    assert "playwright install exited 3" in capsys.readouterr().err
