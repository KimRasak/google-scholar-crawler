"""The environment check: what it reports, and what it must not do to the disk."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.doctor import (  # noqa: E402
    REQUIREMENTS,
    Requirement,
    Status,
    check_bundled_chromium,
    check_channel,
    check_module,
    check_profile,
    check_python,
    check_toml_reader,
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
    finding = check_channel(None)
    assert finding.status is Status.OK
    assert "bundled Chromium" in finding.detail
    assert not finding.fix


def test_an_uninstalled_channel_is_a_warning_with_a_way_out() -> None:
    finding = check_channel("nosuchbrowser")
    assert finding.status is Status.WARN  # the bundled Chromium still works
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
    assert "permissions" in finding.fix


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
        out=tmp_path / "out" / "results.jsonl",
        state=tmp_path / "elsewhere" / "state.json",
        channel="chrome",
    )
    names = [finding.name for finding in findings]
    assert names[:6] == [
        "python",
        "playwright",
        "bs4",
        "lxml",
        "settings files",
        "bundled chromium",
    ]
    assert names[6:] == ["browser channel", "profile", "output", "state"]
    assert _named(findings, "python").status is Status.OK


def test_the_state_check_is_skipped_when_it_shares_the_output_directory(tmp_path: Path) -> None:
    findings = diagnose_environment(
        profile=tmp_path / "profile",
        out=tmp_path / "out" / "results.jsonl",
        state=tmp_path / "out" / "state.json",
        channel=None,
    )
    assert [finding.name for finding in findings].count("state") == 0


def test_the_report_puts_fixes_last_and_ends_on_what_to_do_next(tmp_path: Path) -> None:
    findings = diagnose_environment(
        profile=tmp_path / "profile", out=tmp_path / "x.jsonl", state=tmp_path / "s.json", channel=None
    )
    lines = render_environment(findings)
    assert [line for line in lines if line.startswith("+ python")]
    assert "nothing is broken; these are worth knowing:" in lines  # the fresh profile warns
    assert lines[-1].strip().startswith("profile:")

    clean = [finding for finding in findings if finding.status is Status.OK]
    ready = render_environment(clean)
    assert ready[-1] == "this machine is ready; run --self-check next to test Scholar itself"


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
    finding = check_bundled_chromium()
    assert (finding.status, finding.detail) == (Status.OK, str(browser))


def test_a_browser_that_was_never_downloaded_says_how_to_get_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _absent(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=f"{tmp_path / 'nope'}\n", stderr="")

    monkeypatch.setattr(subprocess, "run", _absent)
    finding = check_bundled_chromium()
    assert finding.status is Status.FAIL
    assert finding.fix == "scholar-crawler --install-browser"


def test_a_probe_that_cannot_import_playwright_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="ModuleNotFoundError: No module named 'playwright'\n"
        )

    monkeypatch.setattr(subprocess, "run", _missing)
    finding = check_bundled_chromium()
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
    assert "driver refused to start" in check_bundled_chromium().detail


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
    assert "[doctor] + bundled chromium" in printed
    assert not (tmp_path / "out").exists()
