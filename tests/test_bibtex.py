"""BibTeX export: popup link discovery, entry parsing, dedup and the fetch path."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import crawler as crawler_module  # noqa: E402
from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    ChallengeKind,
    HumanHandoff,
    Takeover,
)
from scholar_crawler.crawler import Pacing, ScholarCrawler  # noqa: E402
from scholar_crawler.models import ScholarResult  # noqa: E402
from scholar_crawler.parser import (  # noqa: E402
    bibtex_key,
    bibtex_link,
    parse_bibtex,
    parse_result_page,
)
from scholar_crawler.storage import BibtexSink  # noqa: E402
from scholar_crawler.urls import cite_url  # noqa: E402
from tests.fixtures import (  # noqa: E402
    BIBTEX_EXPORT_HTML,
    CITE_POPUP_HTML,
    EMPTY_PAGE_HTML,
    RESULT_PAGE_HTML,
)

NO_DELAY = Pacing(min_delay=0.0, max_delay=0.0, cooldown_every=0, nav_timeout=5.0)
ENTRY = "@article{lovelace1843notes,\n  title={Notes on the Analytical Engine}\n}"
CAPTCHA = Challenge(ChallengeKind.CAPTCHA, "https://www.google.com/sorry/index", "test")


class _FakeMouse:
    def wheel(self, _dx: int, _dy: int) -> None:
        return None


class _FakeLocator:
    """Counts the content markers the crawler checks for on each page kind."""

    def __init__(self, html: str) -> None:
        self._html = html

    def count(self) -> int:
        markers = ('class="gs_r gs_or gs_scl"', 'class="gs_citr"', "<pre", 'class="gs_med"')
        return sum(self._html.count(marker) for marker in markers)


class _FakePage:
    """Serves canned HTML per navigation and records the URLs requested."""

    def __init__(self, pages: list[str]) -> None:
        self._pages = iter(pages)
        self._html = ""
        self.visited: list[str] = []

    def goto(self, url: str, **_kwargs: object) -> None:
        self.visited.append(url)
        self._html = next(self._pages)

    def content(self) -> str:
        return self._html

    def wait_for_timeout(self, _ms: float) -> None:
        return None

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(self._html)

    @property
    def mouse(self) -> _FakeMouse:
        return _FakeMouse()


def _result() -> ScholarResult:
    return parse_result_page(RESULT_PAGE_HTML, query="q").results[0]


def _crawler(page: _FakePage) -> ScholarCrawler:
    return ScholarCrawler(page, HumanHandoff(), NO_DELAY)  # type: ignore[arg-type]


def test_export_link_is_matched_by_path_not_label() -> None:
    href = bibtex_link(CITE_POPUP_HTML)
    assert href is not None
    assert "scholar.bib" in href and "scisf=4" in href
    assert bibtex_link("<html><body>no export links</body></html>") is None


def test_entry_is_read_from_the_rendered_pre_block() -> None:
    entry = parse_bibtex(BIBTEX_EXPORT_HTML)
    assert entry is not None
    assert entry.startswith("@article{lovelace1843notes,")
    assert entry.endswith("}")
    assert bibtex_key(entry) == "lovelace1843notes"


def test_raw_and_unusable_bodies() -> None:
    assert parse_bibtex("@book{key, title={Raw}}") == "@book{key, title={Raw}}"
    assert parse_bibtex("<html><body>Sorry...</body></html>") is None
    assert parse_bibtex("") is None
    assert bibtex_key("not an entry") is None


def test_cite_url_uses_the_card_id_scholar_exposes() -> None:
    result = _result()
    assert result.cluster_id  # the parser records Scholar's data-cid
    url = cite_url(result.cluster_id, language="zh-CN")
    assert f"info%3A{result.cluster_id}%3Ascholar.google.com%2F" in url
    assert "output=cite" in url and "hl=zh-CN" in url


def test_fetch_bibtex_loads_popup_then_export(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage([CITE_POPUP_HTML, BIBTEX_EXPORT_HTML])
    entry = _crawler(page).fetch_bibtex(_result())
    assert entry is not None and entry.startswith("@article{")
    assert len(page.visited) == 2
    assert "output=cite" in page.visited[0]
    assert "scholar.bib" in page.visited[1]


def _seen(seen: list[str], challenge: Challenge) -> Takeover:
    """Record the challenge a stand-in human was handed, and clear it.

    :param seen: list collecting the kinds handed over.
    :param challenge: the challenge handed over.
    :returns: the summary a real wait returns.
    """
    seen.append(challenge.kind.value)
    return Takeover(waited=0.0, saw=(challenge.kind.value,))


def test_profile_records_resolve_their_card_id_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage([RESULT_PAGE_HTML, CITE_POPUP_HTML, BIBTEX_EXPORT_HTML])
    result = _result()
    result.cluster_id = None  # a profile publication carries only its cluster id
    result.cited_by_url = "https://scholar.google.com/scholar?oi=bibs&hl=en&cites=987654321"
    entry = _crawler(page).fetch_bibtex(result)
    assert entry is not None and entry.startswith("@article{")
    assert len(page.visited) == 3
    assert "cluster=987654321" in page.visited[0]
    assert "output=cite" in page.visited[1]
    assert "scholar.bib" in page.visited[2]


def test_records_with_neither_id_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage([])
    result = _result()
    result.cluster_id = None
    result.cited_by_url = None
    assert _crawler(page).fetch_bibtex(result) is None
    assert page.visited == []


def test_an_empty_cluster_listing_yields_no_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage([EMPTY_PAGE_HTML])
    result = _result()
    result.cluster_id = None
    result.cited_by_url = "https://scholar.google.com/scholar?cites=987654321"
    assert _crawler(page).fetch_bibtex(result) is None
    assert len(page.visited) == 1


def test_popup_without_an_export_link_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage(['<html><body><div class="gs_citr">MLA only</div></body></html>'])
    assert _crawler(page).fetch_bibtex(_result()) is None
    assert len(page.visited) == 1


def test_a_challenge_on_the_popup_hands_over_then_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = iter([CAPTCHA, None, None])
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: next(outcomes, None))
    resolved: list[str] = []
    monkeypatch.setattr(
        crawler_module.HumanHandoff,
        "resolve",
        lambda _self, _page, challenge: _seen(resolved, challenge),
    )
    page = _FakePage(["<html><body>captcha</body></html>", CITE_POPUP_HTML, BIBTEX_EXPORT_HTML])
    crawler = _crawler(page)
    entry = crawler.fetch_bibtex(_result())
    assert resolved == ["captcha"]
    assert crawler.handoff_count == 1
    assert entry is not None and entry.startswith("@article{")


def test_bibtex_sink_dedups_by_citation_key(tmp_path: Path) -> None:
    path = tmp_path / "out" / "refs.bib"
    sink = BibtexSink(path)
    sink.open()
    assert sink.write(ENTRY) is True
    assert path.read_text(encoding="utf-8").count("@") == 1, "an entry must survive a crash mid-run"
    assert sink.write(ENTRY) is False
    sink.write("@book{other1900x, title={Other}}")
    sink.close()
    assert (sink.written, sink.skipped) == (2, 1)
    assert path.read_text(encoding="utf-8").count("@") == 2

    reopened = BibtexSink(path)
    reopened.open()
    assert reopened.write(ENTRY) is False
    reopened.close()
    assert path.read_text(encoding="utf-8").count("@") == 2


def test_bibtex_sink_requires_open(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="before open"):
        BibtexSink(tmp_path / "refs.bib").write(ENTRY)
