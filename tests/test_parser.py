"""Parser and URL-building behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.models import SearchRequest  # noqa: E402
from scholar_crawler.parser import parse_result_page  # noqa: E402
from scholar_crawler.urls import parse_cluster_id, search_url  # noqa: E402
from tests.fixtures import EMPTY_PAGE_HTML, RESULT_PAGE_HTML  # noqa: E402


def test_parses_every_card_including_citation_only() -> None:
    page = parse_result_page(RESULT_PAGE_HTML, query="transformer", start=0)
    assert [result.cluster_id for result in page.results] == ["AAA111", "BBB222", "CCC333"]
    assert page.results[2].citation_only is True
    assert page.results[2].link is None


def test_first_card_fields() -> None:
    first = parse_result_page(RESULT_PAGE_HTML, query="transformer", start=10).results[0]
    assert first.title == "Attention is all you need"
    assert first.position == 11
    assert first.page_start == 10
    assert first.link == "https://example.org/paper"
    assert first.resource_link == "https://example.org/paper.pdf"
    assert first.resource_type == "PDF"
    assert first.authors == "A Vaswani, N Shazeer, N Parmar"
    assert first.venue == "Advances in neural information processing systems"
    assert first.year == 2017
    assert first.cited_by_count == 123456
    assert first.cited_by_url == "https://scholar.google.com/scholar?cites=1234567890&as_sdt=2005"
    assert first.versions_count == 89
    assert first.related_url is not None
    assert "Transformer" in first.snippet


def test_relative_title_link_is_absolute() -> None:
    second = parse_result_page(RESULT_PAGE_HTML).results[1]
    assert second.link == "https://scholar.google.com/citations?user=xyz"
    assert second.versions_count is None


def test_bolded_query_terms_do_not_split_words() -> None:
    # Scholar wraps matched terms in <b>, sometimes mid-word.
    assert parse_result_page(RESULT_PAGE_HTML).results[1].title == (
        "Deep residual learning for multi-agents"
    )


def test_total_estimate_skips_non_numeric_banner() -> None:
    assert parse_result_page(RESULT_PAGE_HTML).total_estimate == 1240


def test_total_estimate_ignores_the_page_number_from_page_two_on() -> None:
    page_two = RESULT_PAGE_HTML.replace(
        "About 1,240 results (0.06 sec)", "Page 2 of about 1,240 results (0.06 sec)"
    )
    assert parse_result_page(page_two, start=10).total_estimate == 1240


def test_next_page_detected_only_for_larger_offsets() -> None:
    assert parse_result_page(RESULT_PAGE_HTML, start=0).has_next is True
    assert parse_result_page(RESULT_PAGE_HTML, start=10).has_next is False


def test_empty_page_yields_no_results() -> None:
    page = parse_result_page(EMPTY_PAGE_HTML)
    assert page.results == []
    assert page.has_next is False


def test_search_url_encodes_filters() -> None:
    request = SearchRequest(
        query="graph neural network",
        year_low=2020,
        year_high=2024,
        language="zh-CN",
        sort_by_date=True,
        include_citations=False,
        include_patents=False,
        review_only=True,
    )
    url = search_url(request, start=20)
    assert url.startswith("https://scholar.google.com/scholar?")
    # Parameters are compared parsed, not as substrings: "as_sdt=0" also occurs inside
    # "as_sdt=0,5", which is the opposite setting.
    assert parse_qs(urlsplit(url).query) == {
        "q": ["graph neural network"],
        "hl": ["zh-CN"],
        "start": ["20"],
        "as_ylo": ["2020"],
        "as_yhi": ["2024"],
        "scisbd": ["1"],
        "as_rr": ["1"],
        "as_vis": ["1"],
        "as_sdt": ["0"],
    }


def test_search_url_omits_start_and_defaults_to_english() -> None:
    url = search_url(SearchRequest(query="llm agents"))
    assert parse_qs(urlsplit(url).query) == {
        "q": ["llm agents"],
        "hl": ["en"],
        "as_vis": ["0"],
        "as_sdt": ["0,5"],  # Scholar's own default: patents and citations included
    }


def test_signature_distinguishes_filters() -> None:
    base = SearchRequest(query="x")
    assert base.signature() != SearchRequest(query="x", year_low=2020).signature()
    assert base.signature() == SearchRequest(query="x").signature()


def test_signature_distinguishes_entry_points() -> None:
    assert SearchRequest(cites="42").signature() != SearchRequest(cluster="42").signature()
    assert SearchRequest(query="x").signature() != SearchRequest(query="x", cites="42").signature()


def test_cites_and_cluster_urls() -> None:
    cites_url = search_url(SearchRequest(cites="1234567890"), start=10)
    assert "cites=1234567890" in cites_url
    assert "start=10" in cites_url
    assert "q=" not in cites_url
    assert "cluster=99" in search_url(SearchRequest(cluster="99"))
    both = search_url(SearchRequest(query="transformer", cites="1234567890"))
    assert "cites=1234567890" in both and "q=transformer" in both


def test_request_without_entry_point_is_rejected() -> None:
    with pytest.raises(ValueError, match="needs a query, a cites id or a cluster id"):
        SearchRequest()


def test_label_names_the_entry_point() -> None:
    assert SearchRequest(query="transformer").label == "transformer"
    assert SearchRequest(cites="42").label == "cites:42"
    assert SearchRequest(cluster="42").label == "cluster:42"


@pytest.mark.parametrize(
    "value",
    [
        "1234567890",
        "  1234567890 ",
        "https://scholar.google.com/scholar?cites=1234567890&as_sdt=2005",
        "/scholar?hl=en&cluster=1234567890",
    ],
)
def test_parse_cluster_id_accepts_ids_and_urls(value: str) -> None:
    assert parse_cluster_id(value) == "1234567890"


def test_parse_cluster_id_rejects_unrelated_text() -> None:
    with pytest.raises(ValueError, match="no Scholar cites/cluster id"):
        parse_cluster_id("https://example.org/paper")


def _with_byline(byline: str) -> str:
    """Replace the first card's grey line in the fixture page.

    :param byline: the byline to put in its place.
    :returns: the page HTML.
    """
    original = """A Vaswani, N Shazeer, N Parmar - Advances in neural information
        processing systems, 2017 - proceedings.neurips.cc"""
    assert original in RESULT_PAGE_HTML
    return RESULT_PAGE_HTML.replace(original, byline, 1)


def test_an_arxiv_identifier_is_not_read_as_the_publication_year() -> None:
    # arXiv numbers preprints YYMM.NNNNN, so "arXiv:1910.11945" used to be read as 1910,
    # and stripping that "year" left the venue as "arXiv preprint arXiv:.11945".
    byline = "G Wang, J Leskovec - arXiv preprint arXiv:1910.11945, 2019 - arxiv.org"
    first = parse_result_page(_with_byline(byline)).results[0]
    assert first.year == 2019
    assert first.venue == "arXiv preprint arXiv:1910.11945"


def test_a_year_range_leaves_no_dangling_separator_in_the_venue() -> None:
    byline = "X Wang - The world wide web conference, 2022-2023 - dl.acm.org"
    assert parse_result_page(_with_byline(byline)).results[0].venue == "The world wide web conference"
