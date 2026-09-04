"""The environment check: what it reports, and what it must not do to the disk."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import doctor as doctor_module  # noqa: E402

PYPROJECT_VERSION = next(
    line.split('"')[1]
    for line in (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text().splitlines()
    if line.startswith("version = ")
)
"""The one place the version is declared, read without a TOML parser."""
from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.doctor import (  # noqa: E402
    REQUIREMENTS,
    Finding,
    Requirement,
    Status,
    check_browser,
    check_module,
    check_profile,
    check_python,
    check_toml_reader,
    check_version,
    check_writable,
    diagnose_environment,
    render_environment,
)


def _named(findings: list, name: str):
    return next(finding for finding in findings if finding.name == name)


def test_the_interpreter_running_the_tests_passes_its_own_check() -> None:
    finding = check_python()
    assert finding.status is Status.OK
    assert sys.executable in finding.detail


def test_the_declared_dependencies_are_all_installed_and_new_enough() -> None:
    for requirement in REQUIREMENTS:
        finding = check_module(requirement)
        assert finding.status is Status.OK, finding.detail
        assert finding.detail, requirement.module


def test_a_missing_library_names_the_command_that_installs_it() -> None:
    finding = check_module(Requirement("no_such_module_here", "nothing", (1, 0)))
    assert finding.status is Status.FAIL
    assert "cannot be imported" in finding.detail
    assert finding.fix == "pip install -e ."


def test_a_too_old_library_is_a_failure_not_a_warning() -> None:
    # bs4 is installed; demanding an impossible version exercises the comparison.
    finding = check_module(Requirement("bs4", "beautifulsoup4", (99, 0)))
    assert finding.status is Status.FAIL
    assert "older than the required 99.0" in finding.detail
    assert finding.fix.endswith("--upgrade")


def test_no_channel_means_the_bundled_chromium_is_what_runs() -> None:
    finding = check_browser(None)
    assert finding.status is Status.OK
    assert "bundled Chromium" in finding.detail
    assert not finding.fix


def test_an_uninstalled_channel_stops_the_run_that_would_launch_it() -> None:
    finding = check_browser("nosuchbrowser")
    assert finding.status is Status.FAIL, "the run launches the channel, so it has to exist"
    assert "was not found" in finding.detail
    assert "--channel ''" in finding.fix


def test_a_writable_directory_passes_and_leaves_nothing_behind(tmp_path: Path) -> None:
    finding = check_writable("output", tmp_path / "results.jsonl", kind="file")
    assert finding.status is Status.OK
    assert list(tmp_path.iterdir()) == []


def test_a_directory_that_does_not_exist_yet_is_reported_not_created(tmp_path: Path) -> None:
    finding = check_writable("output", tmp_path / "deep" / "er" / "results.jsonl", kind="file")
    assert finding.status is Status.OK
    assert "does not exist yet" in finding.detail
    assert str(tmp_path) in finding.detail  # the ancestor that was actually probed
    assert not (tmp_path / "deep").exists(), "a diagnostic must not create directories"


def test_an_unwritable_directory_fails_and_says_which_path(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        finding = check_writable("output", locked / "results.jsonl", kind="file")
    finally:
        locked.chmod(0o700)
    assert finding.status is Status.FAIL
    assert str(locked) in finding.detail
    assert "point the flag" in finding.fix


def test_an_unreadable_ancestor_does_not_crash_the_check(tmp_path: Path) -> None:
    # The check exists to replace crashes with advice, so it must not crash itself.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        finding = check_writable("output", locked / "deeper" / "results.jsonl", kind="file")
    finally:
        locked.chmod(0o700)
    assert finding.status is Status.FAIL
    assert "nearest existing" in finding.detail


def test_a_profile_without_cookies_warns_that_a_human_will_be_needed(tmp_path: Path) -> None:
    finding = check_profile(tmp_path / "profile")
    assert finding.status is Status.WARN
    assert "no cookies yet" in finding.detail
    assert "takeover on the first run" in finding.fix


def test_a_profile_with_cookies_passes(tmp_path: Path) -> None:
    cookies = tmp_path / "profile" / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"sqlite")
    finding = check_profile(tmp_path / "profile")
    assert finding.status is Status.OK
    assert "carries cookies from earlier runs" in finding.detail


def test_the_full_check_covers_every_prerequisite(tmp_path: Path) -> None:
    findings = diagnose_environment(
        profile=tmp_path / "profile",
        written=[
            ("--out", tmp_path / "out" / "results.jsonl", "file"),
            ("--state", tmp_path / "elsewhere" / "state.json", "file"),
            ("--dump-html", tmp_path / "dump", "dir"),
        ],
        channel="chrome",
    )
    names = [finding.name for finding in findings]
    assert names == [
        "python",
        "version",
        "playwright",
        "bs4",
        "lxml",
        "settings files",
        "browser",
        "profile",
        "--out",
        "--state",
        "--dump-html",
    ]
    assert _named(findings, "python").status is Status.OK


def test_one_directory_is_checked_once_however_many_flags_land_in_it(tmp_path: Path) -> None:
    # Several paths in one place is one thing to fix, and a report that says so three times
    # reads like three problems.
    findings = diagnose_environment(
        profile=tmp_path / "profile",
        written=[
            ("--out", tmp_path / "out" / "results.jsonl", "file"),
            ("--state", tmp_path / "out" / "state.json", "file"),
            ("--challenge-log", tmp_path / "out" / "challenges.jsonl", "file"),
            ("--profile", tmp_path / "profile", "dir"),
        ],
        channel=None,
    )
    names = [finding.name for finding in findings]
    assert names.count("--out") == 1
    assert "--state" not in names and "--challenge-log" not in names
    assert names.count("profile") == 1, "the profile is reported once, for its cookies"


def test_the_report_puts_fixes_last_and_ends_on_what_to_do_next(tmp_path: Path) -> None:
    findings = diagnose_environment(
        profile=tmp_path / "profile",
        written=[("--out", tmp_path / "x.jsonl", "file")],
        channel=None,
    )
    lines = render_environment(findings)
    assert [line for line in lines if line.startswith("+ python")]
    assert "nothing is broken; these are worth knowing:" in lines  # the fresh profile warns
    assert lines[-1].strip().startswith("profile:")

    clean = [finding for finding in findings if finding.status is Status.OK]
    ready = render_environment(clean)
    assert ready[-1] == "this machine is ready; run --self-check next to test Scholar itself"


def test_a_note_is_not_listed_under_the_problems_to_fix(tmp_path: Path) -> None:
    # The first thing a new install sees must not overstate what is wrong with it: a fresh
    # profile is a note, and printing it under "1 problem must be fixed" reads as a second one.
    broken = Finding(
        "browser", Status.FAIL, "bundled Chromium not downloaded", "scholar-crawler --install-browser"
    )
    note = Finding("profile", Status.WARN, "holds no cookies yet", "expect one takeover")
    lines = render_environment([broken, note])

    problems = lines.index("1 problem must be fixed before a crawl can run:")
    notes = lines.index("also worth knowing, but nothing to fix:")
    assert problems < notes
    assert lines[problems + 1 : notes] == ["  browser: scholar-crawler --install-browser"]
    assert lines[notes + 1 :] == ["  profile: expect one takeover"]

    # A problem with nothing else to say ends on the fix, with no empty heading after it.
    assert render_environment([broken])[-1] == "  browser: scholar-crawler --install-browser"


def test_the_browser_probe_keeps_only_the_path_the_child_printed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Playwright 1.62 prints asyncio teardown noise from a driver that never launched a
    # browser, and the first command a new user runs should not show it.
    browser = tmp_path / "chrome"
    browser.write_text("", encoding="utf-8")

    def _noisy(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, stdout=f"{browser}\n", stderr="Task was destroyed but it is pending!\n"
        )

    monkeypatch.setattr(subprocess, "run", _noisy)
    finding = check_browser(None)
    assert finding.status is Status.OK
    assert finding.detail == f"bundled Chromium at {browser} (no channel requested)"


def test_a_browser_that_was_never_downloaded_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _absent(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=f"{tmp_path / 'nope'}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _absent)
    finding = check_browser(None)
    assert finding.status is Status.FAIL
    assert finding.fix == "scholar-crawler --install-browser"


def test_a_missing_download_is_not_a_problem_when_the_channel_is_installed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A run launches Chrome, so the 550 MB download it never opens must not fail --doctor;
    # a crawl was verified to work in exactly this state.
    def _absent(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=f"{tmp_path / 'nope'}\n", stderr="")

    chrome = tmp_path / "Google Chrome"
    chrome.write_text("", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _absent)
    monkeypatch.setattr(doctor_module, "CHROME_PATHS", {"chrome": (str(chrome),)})
    finding = check_browser("chrome")
    assert finding.status is Status.OK
    assert "no bundled Chromium as a spare" in finding.detail
    assert not finding.fix


def test_a_probe_that_cannot_import_playwright_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="ModuleNotFoundError: No module named 'playwright'\n"
        )

    monkeypatch.setattr(subprocess, "run", _missing)
    finding = check_browser(None)
    assert finding.detail == "playwright is not installed"
    assert finding.fix == "pip install -e ."


def test_a_probe_that_fails_for_another_reason_keeps_the_last_error_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="Traceback...\nOSError: driver refused to start\n"
        )

    monkeypatch.setattr(subprocess, "run", _broken)
    assert "driver refused to start" in check_browser(None).detail


def test_the_toml_reader_is_checked_before_a_config_run_needs_it() -> None:
    # pyproject declares the backport for 3.10, so every supported interpreter can read one.
    finding = check_toml_reader()
    assert finding.name == "settings files"
    assert finding.status is Status.OK, finding.detail
    assert "--config" in finding.detail


def test_doctor_reports_through_the_cli_and_sends_no_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "--doctor",
            "--profile",
            str(tmp_path / "profile"),
            "--out",
            str(tmp_path / "out" / "results.jsonl"),
            "--state",
            str(tmp_path / "out" / "state.json"),
        ]
    )
    printed = capsys.readouterr().out
    assert exit_code == 0  # this machine runs the tests, so nothing is broken here
    assert "[doctor] + python" in printed
    assert "[doctor] + browser" in printed
    assert not (tmp_path / "out").exists()


def test_an_editable_install_is_told_when_its_metadata_went_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pip writes the version once; after a git pull the tool can report a version it is not
    # running, and that number goes into every --json document and every bug report.
    monkeypatch.setattr(doctor_module, "version", lambda: "0.1.0")
    monkeypatch.setattr(doctor_module, "_source_version", lambda: "0.2.0")
    finding = check_version()
    assert finding.status is Status.WARN
    assert finding.detail == "pip recorded 0.1.0 but this checkout is 0.2.0"
    assert "pip install -e ." in finding.fix

    monkeypatch.setattr(doctor_module, "_source_version", lambda: "0.1.0")
    assert check_version().status is Status.OK
    monkeypatch.setattr(doctor_module, "_source_version", lambda: None)
    assert check_version().status is Status.OK, "an install without sources cannot be compared"


def test_the_version_in_the_sources_is_the_one_pyproject_declares() -> None:
    assert doctor_module._source_version() == PYPROJECT_VERSION
