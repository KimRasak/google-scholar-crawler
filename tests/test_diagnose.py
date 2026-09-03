"""Failures, as the operator reads them: what happened and what to do next."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import Error as PlaywrightError  # noqa: E402

from scholar_crawler.diagnose import (  # noqa: E402
    CrawlFailure,
    Failure,
    diagnose_challenge_loop,
    diagnose_navigation,
    diagnose_page,
)

URL = "https://scholar.google.com/scholar?q=x"


def _failure_of(message: str) -> Failure:
    return diagnose_navigation(PlaywrightError(message), URL).failure


def test_each_network_failure_is_named_from_the_browser_error() -> None:
    assert _failure_of("Page.goto: net::ERR_CONNECTION_REFUSED at https://x") is Failure.CONNECTION_REFUSED
    assert _failure_of("net::ERR_NAME_NOT_RESOLVED") is Failure.DNS
    assert _failure_of("net::ERR_INTERNET_DISCONNECTED") is Failure.OFFLINE
    assert _failure_of("net::ERR_PROXY_CONNECTION_FAILED") is Failure.PROXY
    # Chromium refuses its own blocked ports before connecting, which a --host typo can hit.
    assert _failure_of("net::ERR_UNSAFE_PORT at http://127.0.0.1:9/") is Failure.CONNECTION_REFUSED
    assert _failure_of("net::ERR_TUNNEL_CONNECTION_FAILED") is Failure.PROXY
    assert _failure_of("net::ERR_CONNECTION_RESET") is Failure.RESET
    assert _failure_of("net::ERR_EMPTY_RESPONSE") is Failure.RESET
    assert _failure_of("net::ERR_CERT_AUTHORITY_INVALID") is Failure.CERTIFICATE
    assert _failure_of("Timeout 45000ms exceeded") is Failure.TIMEOUT
    assert _failure_of("Target page, context or browser has been closed") is Failure.BROWSER_CLOSED


def test_an_unrecognized_error_keeps_its_text_and_still_suggests_something() -> None:
    diagnosis = diagnose_navigation(PlaywrightError("something entirely new\nCall log:\n  - navigating"), URL)
    assert diagnosis.failure is Failure.UNKNOWN
    assert "not one this tool recognizes" in diagnosis.what
    assert diagnosis.detail == "something entirely new"  # only the first line, not the call log
    assert any("--self-check" in step for step in diagnosis.next_steps)


def test_every_diagnosis_names_the_url_and_offers_a_next_step() -> None:
    messages = (
        "net::ERR_CONNECTION_REFUSED",
        "net::ERR_NAME_NOT_RESOLVED",
        "net::ERR_INTERNET_DISCONNECTED",
        "net::ERR_PROXY_CONNECTION_FAILED",
        "net::ERR_CONNECTION_RESET",
        "net::ERR_CERT_DATE_INVALID",
        "Timeout 45000ms exceeded",
        "Target closed",
        "who knows",
    )
    for message in messages:
        diagnosis = diagnose_navigation(PlaywrightError(message), URL)
        assert URL in diagnosis.what, message
        assert diagnosis.next_steps, message
        assert all(step and not step.endswith(".") for step in diagnosis.next_steps), message


def test_a_refusal_to_serve_is_told_apart_from_a_broken_server() -> None:
    limited = diagnose_page(URL, status=429, title="Sorry", dump=None)
    assert limited.failure is Failure.RATE_LIMITED
    assert "refusing requests" in limited.what
    assert any("--resume" in step for step in limited.next_steps)

    assert diagnose_page(URL, status=503, title="", dump=None).failure is Failure.RATE_LIMITED

    broken = diagnose_page(URL, status=500, title="Error 500", dump=None)
    assert broken.failure is Failure.HTTP_ERROR
    assert "HTTP 500" in broken.what
    assert broken.detail == "page title: Error 500"


def test_an_unreadable_page_points_at_the_parser_and_the_saved_copy(tmp_path: Path) -> None:
    saved = tmp_path / "dump" / "empty-0.html"
    diagnosis = diagnose_page(URL, status=200, title="Wi-Fi login", dump=saved)
    assert diagnosis.failure is Failure.UNKNOWN_LAYOUT
    assert "carries none of Scholar's markers" in diagnosis.what
    assert any(str(saved) in step for step in diagnosis.next_steps)
    assert any("parser.py" in step for step in diagnosis.next_steps)

    without_dump = diagnose_page(URL, status=200, title="", dump=None)
    assert any("--dump-html" in step for step in without_dump.next_steps)


def test_a_page_that_keeps_challenging_is_reported_as_being_blocked() -> None:
    diagnosis = diagnose_challenge_loop(URL, 3)
    assert diagnosis.failure is Failure.RATE_LIMITED
    assert "3 times in a row" in diagnosis.what
    assert "being blocked rather than merely checked" in diagnosis.what
    assert any("challenges.jsonl" in step for step in diagnosis.next_steps)


def test_only_failures_a_retry_could_survive_are_retried() -> None:
    # Retrying a refused connection or a bad name just delays the message by 15 seconds.
    assert not diagnose_navigation(PlaywrightError("net::ERR_CONNECTION_REFUSED"), URL).retry_worthwhile
    assert not diagnose_navigation(PlaywrightError("net::ERR_NAME_NOT_RESOLVED"), URL).retry_worthwhile
    assert not diagnose_navigation(PlaywrightError("net::ERR_CERT_DATE_INVALID"), URL).retry_worthwhile
    assert not diagnose_navigation(PlaywrightError("net::ERR_PROXY_CONNECTION_FAILED"), URL).retry_worthwhile
    assert diagnose_navigation(PlaywrightError("Timeout 45000ms exceeded"), URL).retry_worthwhile
    assert diagnose_navigation(PlaywrightError("net::ERR_CONNECTION_RESET"), URL).retry_worthwhile
    assert diagnose_navigation(PlaywrightError("who knows"), URL).retry_worthwhile


def test_a_diagnosis_renders_as_what_happened_then_what_to_do() -> None:
    lines = diagnose_page(URL, status=500, title="Error 500", dump=None).render()
    assert lines[0].startswith("Scholar answered HTTP 500")
    assert all(line.startswith("try: ") for line in lines[1:-1])
    assert lines[-1] == "underlying error: page title: Error 500"


def test_the_exception_message_is_the_first_line_of_the_diagnosis() -> None:
    diagnosis = diagnose_page(URL, status=429, title="", dump=None)
    with pytest.raises(CrawlFailure, match="refusing requests"):
        raise CrawlFailure(diagnosis)
    assert CrawlFailure(diagnosis).diagnosis is diagnosis
