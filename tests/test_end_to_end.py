"""End to end: a real browser, real navigation, a challenge, a takeover and the files.

Everything here runs against :mod:`tests.fakescholar` on loopback, so the whole path the
crawler actually takes — Playwright navigation, challenge detection, handing the page to a
human, resuming, appending JSONL, recording the cursor and the takeover — is exercised on
every test run without contacting Google.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import crawler as crawler_module  # noqa: E402
from scholar_crawler.browser import BrowserOptions, Session, browser_session  # noqa: E402
from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    HumanHandoff,
    Takeover,
    detect_challenge,
)
from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.crawler import Pacing, ScholarCrawler  # noqa: E402
from scholar_crawler.expand import FollowPolicy  # noqa: E402
from scholar_crawler.models import AuthorRequest, SearchRequest  # noqa: E402
from scholar_crawler.run import CrawlLimits, Outputs, crawl, crawl_targets  # noqa: E402
from scholar_crawler.storage import ChallengeLog, StateStore  # noqa: E402
from tests.fakescholar import FakeScholar, serving  # noqa: E402

NO_WAIT = Pacing(min_delay=0.0, max_delay=0.0, cooldown_every=0, challenge_cooldown=0.0)
TEMPLATE = SearchRequest(cites="0", language="en")
"""The filter template expansion inherits; its id is replaced per record."""

QUERY = SearchRequest(query="graph attention")
"""The listing every crawl in this module pages through."""


class _StandInHuman(HumanHandoff):
    """Clears a challenge the way a person does: deal with the page, then reload it.

    :param reloads: pages reloaded, so a test can assert the takeover really happened.
    """

    def __init__(self) -> None:
        super().__init__(timeout=5.0, poll_interval=0.0)
        self.reloads = 0

    def resolve(self, page: Page, challenge: Challenge) -> Takeover:
        """Reload until the page carries content again.

        :param page: the challenged page.
        :param challenge: the detected challenge.
        :returns: the takeover summary a real wait would return.
        """
        self.reloads += 1
        page.reload(wait_until="domcontentloaded")
        assert detect_challenge(page) is None, "the stand-in human failed to clear the page"
        return Takeover(waited=0.0, saw=(challenge.kind.value,))


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove pacing and dwell sleeps; the rhythm is unit-tested elsewhere."""
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)


@pytest.fixture
def page(tmp_path: Path) -> Iterator[Page]:
    """A real headless browser page in a throwaway profile."""
    options = BrowserOptions(user_data_dir=tmp_path / "profile", headless=True, channel=None)
    with browser_session(options) as (_context, browser_page):
        yield browser_page


def _outputs(tmp_path: Path) -> Outputs:
    return Outputs.open_for(
        out=tmp_path / "results.jsonl",
        state=tmp_path / "state.json",
        profiles=tmp_path / "profiles.jsonl",
        csv=tmp_path / "results.csv",
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_crawl_pages_through_a_site_and_writes_what_it_found(page: Page, tmp_path: Path) -> None:
    site = FakeScholar(pages=3)
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        crawler = ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host)
        crawl_targets(
            crawler,
            CrawlLimits(pages=2),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )
        outputs.close_and_report(crawler)

    assert site.offsets_requested() == [0, 10]  # the page budget stopped it, not the site
    records = _records(tmp_path / "results.jsonl")
    assert len(records) == 20
    assert [record["page_start"] for record in records[:2]] == [0, 0]
    assert records[10]["page_start"] == 10
    assert all(record["query"] == "graph attention" for record in records)
    assert (tmp_path / "results.csv").read_text(encoding="utf-8").count("\n") == 21  # header + rows

    state = StateStore(tmp_path / "state.json")
    state.load()
    assert state.next_start(QUERY.signature()) == 20
    assert crawler.stats().requests == 2


def test_a_challenge_hands_over_and_the_crawl_finishes(page: Page, tmp_path: Path) -> None:
    site = FakeScholar(pages=2, challenge_at=(10,))
    outputs = _outputs(tmp_path)
    human = _StandInHuman()
    with serving(site) as host:
        crawler = ScholarCrawler(
            page,
            human,
            NO_WAIT,
            host=host,
            dump_dir=tmp_path / "dump",
            challenge_log=ChallengeLog(tmp_path / "challenges.jsonl"),
        )
        crawl_targets(
            crawler,
            CrawlLimits(pages=2),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )
        outputs.close_and_report(crawler)

    assert site.challenges_served == [10]
    assert human.reloads == 1
    # The challenged offset is asked for three times: the load that got the challenge, the
    # human's reload, and the crawler's own retry after the handoff returned.
    assert site.offsets_requested() == [0, 10, 10, 10]
    assert len(_records(tmp_path / "results.jsonl")) == 20  # nothing was lost to the challenge

    stats = crawler.stats()
    assert (stats.handoffs, stats.challenges) == (1, {"captcha": 1})
    assert crawler.consecutive_handoffs == 0  # the successful page reset the streak

    takeovers = json.loads((tmp_path / "challenges.jsonl").read_text(encoding="utf-8").strip())
    assert takeovers["kind"] == "captcha"
    assert takeovers["outcome"] == "resolved"
    assert takeovers["saw"] == ["captcha"]
    assert takeovers["target"] == "10"
    # The logged URL keeps what explains the takeover and redacts everything else, so the
    # log stays safe to keep and share.
    assert "q=graph+attention" in takeovers["url"] and "start=10" in takeovers["url"]
    assert "as_vis=REDACTED" in takeovers["url"]
    assert [path.name for path in (tmp_path / "dump").glob("*captcha*")], "the challenge was dumped"


def test_a_resumed_run_continues_where_the_first_one_stopped(page: Page, tmp_path: Path) -> None:
    site = FakeScholar(pages=4)
    with serving(site) as host:
        first = _outputs(tmp_path)
        crawler = ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host)
        crawl_targets(
            crawler,
            CrawlLimits(pages=2),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            first,
        )
        first.close_and_report(crawler)

        second = _outputs(tmp_path)
        crawl_targets(
            ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host),
            CrawlLimits(pages=2, resume=True),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            second,
        )
        second.close_and_report(None)

    assert site.offsets_requested() == [0, 10, 20, 30]
    assert len(_records(tmp_path / "results.jsonl")) == 40
    state = StateStore(tmp_path / "state.json")
    state.load()
    assert state.next_start(QUERY.signature()) == 40


def test_an_author_profile_is_crawled_and_its_header_stored(page: Page, tmp_path: Path) -> None:
    site = FakeScholar()
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        crawler = ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host)
        crawl_targets(
            crawler,
            CrawlLimits(pages=1),
            [],
            [AuthorRequest(user_id="kukA0LcAAAAJ")],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )
        outputs.close_and_report(crawler)

    assert any(request.startswith("/citations") for request in site.requests)
    records = _records(tmp_path / "results.jsonl")
    assert [record["title"] for record in records] == [
        "Notes on the Analytical Engine",
        "An uncited draft",
    ]
    profiles = _records(tmp_path / "profiles.jsonl")
    assert profiles[0]["name"] == "Ada Lovelace"
    assert (profiles[0]["cited_by_total"], profiles[0]["h_index"]) == (12345, 57)


def test_a_clean_crawl_raises_no_audit_alarm(
    page: Page, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = FakeScholar(pages=1)
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        crawler = ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host)
        crawl_targets(
            crawler,
            CrawlLimits(pages=1),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )
        outputs.close_and_report(crawler)
    printed = capsys.readouterr().out
    assert "[out] 10 new records" in printed
    assert "[audit]" not in printed


def test_a_run_described_entirely_by_a_settings_file_collects_the_same_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The file has to reach the browser, not just the parser: host, profile and outputs all
    # come from disk here, and nothing is passed as a flag except the file itself.
    site = FakeScholar(pages=1)
    with serving(site) as host:
        settings = tmp_path / "scholar.toml"
        settings.write_text(
            'query = ["graph attention"]\n'
            "pages = 1\n"
            "headless = true\n"
            'channel = ""\n'
            f'host = "{host}"\n'
            f'profile = "{tmp_path / "profile"}"\n'
            f'out = "{tmp_path / "results.jsonl"}"\n'
            f'state = "{tmp_path / "state.json"}"\n'
            f'challenge-log = "{tmp_path / "challenges.jsonl"}"\n'
            "\n[pacing]\nmin-delay = 0.0\nmax-delay = 0.0\ncooldown-every = 0\n",
            encoding="utf-8",
        )
        assert main(["--config", str(settings)]) == 0

    printed = capsys.readouterr().out
    assert f"[config] 12 setting(s) from {settings}" in printed
    assert "[out] 10 new records" in printed
    assert len(_records(tmp_path / "results.jsonl")) == 10
    assert site.offsets_requested() == [0]


def test_an_unattended_challenge_stops_the_run_without_losing_what_it_had(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # crawl() owns the browser, so this covers opening it, failing mid-run, and still
    # closing and reporting the files — the promise that a stopped run keeps its data.
    site = FakeScholar(pages=3, challenge_at=(10,))
    with serving(site) as host:
        session = Session(
            options=BrowserOptions(user_data_dir=tmp_path / "profile", headless=True, channel=None),
            handoff=HumanHandoff(timeout=1.0, poll_interval=0.0, headless=True),
            log=ChallengeLog(tmp_path / "challenges.jsonl"),
            host=host,
        )
        outputs = _outputs(tmp_path)
        outcome = crawl(
            session, NO_WAIT, CrawlLimits(pages=3), [QUERY], [], FollowPolicy(), TEMPLATE, outputs
        )

    assert (outcome.exit_code, outcome.kind) == (1, "challenge_unattended")
    assert "without --headless" in " ".join(outcome.next_steps)
    assert len(_records(tmp_path / "results.jsonl")) == 10  # page one survived the stop
    assert "[out] 10 new records" in capsys.readouterr().out
    recorded = ChallengeLog(tmp_path / "challenges.jsonl").entries()
    assert [entry.outcome for entry in recorded] == ["unattended"]

    state = StateStore(tmp_path / "state.json")
    state.load()
    assert state.next_start(QUERY.signature()) == 10  # resuming retries the challenged page


def _session(tmp_path: Path, host: str) -> Session:
    return Session(
        options=BrowserOptions(user_data_dir=tmp_path / "profile", headless=True, channel=None),
        handoff=HumanHandoff(timeout=1.0, poll_interval=0.0, headless=True),
        log=ChallengeLog(tmp_path / "challenges.jsonl"),
        host=host,
        dump_dir=tmp_path / "dump",
    )


def _stderr_of(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err


def test_a_host_that_refuses_the_connection_is_explained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Bind a port, then close it, so nothing is listening on an address that resolves.
    with serving(FakeScholar()) as host:
        pass

    outputs = _outputs(tmp_path)
    outcome = crawl(
        _session(tmp_path, host),
        NO_WAIT,
        CrawlLimits(pages=1),
        [QUERY],
        [],
        FollowPolicy(),
        TEMPLATE,
        outputs,
    )

    assert (outcome.exit_code, outcome.kind) == (1, "connection_refused")
    assert outcome.next_steps, "a caller reading --json needs something to do next"
    captured = capsys.readouterr()
    assert "refused the connection" in captured.err
    assert "try: open the same address in a normal browser" in captured.err
    assert "underlying error:" in captured.err  # the raw error stays when the guess is wrong
    # A refused connection answers the same way every time, so it is not retried.
    assert "1 request in" in captured.out and "0 navigation retries" in captured.out


def test_a_page_this_tool_cannot_read_is_not_reported_as_no_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = FakeScholar(body="<html><head><title>Wi-Fi login</title></head><body>sign in</body></html>")
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        outcome = crawl(
            _session(tmp_path, host),
            NO_WAIT,
            CrawlLimits(pages=2),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )

    assert (outcome.exit_code, outcome.kind) == (1, "unknown_layout")
    assert len(site.requests) == 1  # it stopped instead of paging through a site it cannot read
    printed = _stderr_of(capsys)
    assert "carries none of Scholar's markers" in printed
    assert "try: run --self-check" in printed
    assert "page title: Wi-Fi login" in printed
    assert [path.name for path in (tmp_path / "dump").glob("*empty*")], "the page was saved"
    assert str(next((tmp_path / "dump").glob("*empty*"))) in printed


def test_a_refusal_to_serve_is_reported_as_a_block_not_a_bug(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = FakeScholar(status=429, body="<html><head><title>Sorry</title></head><body>too many</body></html>")
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        outcome = crawl(
            _session(tmp_path, host),
            NO_WAIT,
            CrawlLimits(pages=1),
            [QUERY],
            [],
            FollowPolicy(),
            TEMPLATE,
            outputs,
        )

    assert (outcome.exit_code, outcome.kind) == (1, "rate_limited")
    printed = _stderr_of(capsys)
    assert "HTTP 429" in printed and "refusing requests from here" in printed
    assert "try: stop for a while" in printed


def test_a_zero_hit_listing_is_still_an_ordinary_empty_result(
    page: Page, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Scholar's own "did not match any articles" notice is content, not a failure.
    site = FakeScholar(pages=0)
    outputs = _outputs(tmp_path)
    with serving(site) as host:
        crawler = ScholarCrawler(page, _StandInHuman(), NO_WAIT, host=host)
        crawl_targets(
            crawler, CrawlLimits(pages=2), [QUERY], [], FollowPolicy(), TEMPLATE, outputs
        )
        outputs.close_and_report(crawler)

    printed = capsys.readouterr().out
    assert "parsed=0" in printed
    assert "[out] 0 new records" in printed
    assert _records(tmp_path / "results.jsonl") == []
