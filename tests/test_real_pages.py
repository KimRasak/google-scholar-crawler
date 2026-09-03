"""Parsing real Scholar markup.

The fixtures under ``tests/pages`` are sanitized copies of pages the crawler actually
loaded (see :mod:`tests.sanitize`). Hand-written fixtures prove the parser's logic; these
prove it still matches Scholar's real structure, and they fail when a refresh brings in
markup the parser no longer understands.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bs4 import BeautifulSoup  # noqa: E402

from scholar_crawler.analysis import render_summary, summarize  # noqa: E402
from scholar_crawler.audit import audit_records, render_audit  # noqa: E402
from scholar_crawler.challenge import RESULTS_SELECTOR  # noqa: E402
from scholar_crawler.graph import build_graph, render_network  # noqa: E402
from scholar_crawler.parser import (  # noqa: E402
    bibtex_key,
    bibtex_link,
    parse_author_page,
    parse_bibtex,
    parse_result_page,
)
from scholar_crawler.selfcheck import check_page, report  # noqa: E402
from tests.fixtures import EMPTY_PAGE_HTML  # noqa: E402
from tests.sanitize import sanitize  # noqa: E402

PAGES = Path(__file__).parent / "pages"

RESULTS_SELECTOR_HOMES = {
    "div.gs_r.gs_or.gs_scl": "results.html",
    "div#gs_res_ccl_mid": "results.html",
    "#gsc_a_b": "author.html",
    "#gsc_prf_in": "author.html",
    # The "did not match any articles" box: a real page with no hits is not a challenge, and
    # no captured fixture is empty, so the hand-written EMPTY_PAGE_HTML stands in for it.
    "div.gs_med": None,
}
"""Which captured page each part of :data:`RESULTS_SELECTOR` is expected to match."""


def _read(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def test_a_real_result_page_passes_every_self_check(capsys: pytest.CaptureFixture[str]) -> None:
    page = parse_result_page(_read("results.html"), query="graph attention networks")
    assert report(check_page(page)) is True
    assert "all 10 checks passed" in capsys.readouterr().out


def test_a_real_result_page_yields_complete_records() -> None:
    page = parse_result_page(_read("results.html"), query="graph attention networks")
    assert len(page.results) == 6
    assert page.total_estimate is not None and page.total_estimate > 1_000_000
    assert page.has_next is True

    first = page.results[0]
    assert first.title == "Graph attention networks"
    assert first.year == 2017
    assert first.cited_by_count is not None and first.cited_by_count > 40_000
    assert first.cluster_id and re.fullmatch(r"[\w-]{8,}", first.cluster_id)
    assert first.cited_by_url and "cites=" in first.cited_by_url
    assert first.versions_url and "cluster=" in first.versions_url
    assert first.byline.startswith("P Veličković")
    assert first.snippet
    assert any(result.resource_link for result in page.results)
    assert {result.resource_type for result in page.results} & {"PDF", "HTML", "BOOK"}


def test_a_real_profile_page_yields_stats_and_publications() -> None:
    author = parse_author_page(_read("author.html"), user_id="kukA0LcAAAAJ")
    profile = author.profile
    assert profile.name == "Yoshua Bengio"
    assert profile.affiliation and "computer science" in profile.affiliation
    assert profile.interests[:2] == ["Machine learning", "deep learning"]
    assert profile.cited_by_total is not None and profile.cited_by_total > 1_000_000
    assert profile.h_index and profile.i10_index
    assert profile.cited_by_recent and profile.cited_by_recent < profile.cited_by_total
    assert author.has_more is True

    assert [result.year for result in author.results] == [2014, 2015, 2016]
    top = author.results[0]
    assert top.title == "Generative adversarial nets"
    assert top.cited_by_count is not None and top.cited_by_count > 100_000
    assert top.extra["citation_id"].startswith("kukA0LcAAAAJ:")
    assert top.cluster_id is None  # profile rows carry no data-cid; BibTeX resolves it


def test_a_real_cite_popup_and_export_round_trip() -> None:
    href = bibtex_link(_read("cite_popup.html"))
    assert href is not None
    assert "scholar.bib" in href or "scisf=4" in href

    entry = parse_bibtex(_read("bibtex.html"))
    assert entry.startswith("@article{")
    # Scholar's own key keeps the transliterated author name.
    assert bibtex_key(entry) == "velivckovic2017graph"
    assert "Graph attention networks" in entry


def _real_records() -> list[dict[str, object]]:
    """Collect the records a real search page and a real profile page yield.

    :returns: six search results followed by three profile publications.
    """
    page = parse_result_page(_read("results.html"), query="graph attention networks")
    author = parse_author_page(_read("author.html"), user_id="kukA0LcAAAAJ")
    return [result.to_dict() for result in (*page.results, *author.results)]


def test_the_audit_of_real_records_reports_only_what_scholar_really_did(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The audit exists to say what is wrong with collected data, and until now it only ever saw
    # data written to trip it. On real records it must find no error at all, and the warnings it
    # does raise must be things Scholar itself did.
    records = _real_records()
    findings = audit_records(records)
    tripped = {finding.check.name: finding.check.severity for finding in findings}

    assert set(tripped) == {"venue_truncated", "authors_truncated"}, tripped
    assert set(tripped.values()) == {"warn"}
    # Every profile row lacks a data-cid by construction; calling that a defect made a whole
    # author collection look broken while nothing was wrong with it.
    assert "cluster_id_missing" not in tripped

    for line in render_audit(findings, len(records)):
        print(line)
    printed = capsys.readouterr().out
    assert "0 errors, 2 warnings" in printed


def test_the_overview_of_real_records_keeps_scholars_own_cuts_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    records = _real_records()
    for line in render_summary(summarize(records)):
        print(line)
    printed = capsys.readouterr().out

    assert "records          9" in printed
    assert "unknown year     0" in printed
    # Scholar elides long venue names, and the real journal is "… and Data Engineering": a
    # grouped listing that dropped the mark would name a journal that does not exist.
    assert "IEEE Transactions on Knowledge and Data …" in printed
    assert "arXiv preprint" in printed  # two spellings of it, collapsed into one label


def test_the_network_of_real_records_says_why_it_has_no_edges(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Edges come from --cites listings; a plain search collection has none, and the report has to
    # say so rather than print an empty graph.
    for line in render_network(build_graph(_real_records())):
        print(line)
    printed = capsys.readouterr().out

    assert "no citation edges in 9 records" in printed
    assert "--cites" in printed


def test_the_fixtures_carry_no_session_material() -> None:
    for path in sorted(PAGES.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        assert "<script" not in text.lower(), path.name
        assert not re.search(r"(scisig|xsrf|csrf)(=|%3D)(?!REDACTED)[A-Za-z0-9_%\-]{8,}", text), path.name
        assert not re.search(r"[A-Za-z0-9_-]{40,}", text), path.name
        assert not re.search(r"Verified email at (?!example\.edu)", text), path.name


def test_sanitizing_strips_scripts_tokens_and_extra_cards() -> None:
    html = """<html><head><script>var token = 1;</script><style>a{}</style></head><body>
      <img src="https://scholar.google.com/citations/images/avatar?sig=ABCDEFGHIJKL">
      <form action="/citations?xsrf=SECRETVALUE12345">
        <input name="xsrf" type="hidden" value="SECRETVALUE12345">
      </form>
      <a href="/scholar?q=x&amp;scisig=SECRETVALUE12345&amp;scisf=4" onclick="go()">cite</a>
      <a href="/continue?next=%2Fscholar%3Fscisig%3DSECRETVALUE12345">nested</a>
      <div class="gs_r gs_or gs_scl">one</div>
      <div class="gs_r gs_or gs_scl">two</div>
      <div class="gs_r gs_or gs_scl">three</div>
      <p>Verified email at mit.edu</p>
    </body></html>"""
    cleaned = sanitize(html, max_cards=2)
    assert "<script" not in cleaned and "<style" not in cleaned
    assert "SECRETVALUE12345" not in cleaned
    assert "scisf=4" in cleaned  # the parser matches on this, so it must survive
    assert 'src="about:blank"' in cleaned
    assert "onclick" not in cleaned
    assert cleaned.count('class="gs_r gs_or gs_scl"') == 2
    assert "Verified email at example.edu" in cleaned


def test_sanitizing_leaves_plain_links_alone() -> None:
    cleaned = sanitize('<a href="/scholar?cites=123&amp;hl=en">cited by</a>')
    assert "cites=123" in cleaned
    assert "hl=en" in cleaned


def test_every_part_of_the_results_selector_still_matches_a_page() -> None:
    # A page is read as a challenge when nothing here matches, so a selector that silently
    # stops matching Scholar's markup turns every ordinary page into a takeover prompt.
    parts = [part.strip() for part in RESULTS_SELECTOR.split(",")]
    assert set(parts) == set(RESULTS_SELECTOR_HOMES), "a new selector part needs a page to prove it"
    for part, page in RESULTS_SELECTOR_HOMES.items():
        html = EMPTY_PAGE_HTML if page is None else _read(page)
        where = page or "an empty result page"
        assert BeautifulSoup(html, "lxml").select(part), f"{part} matches nothing in {where}"
