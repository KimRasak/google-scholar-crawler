"""Author-profile parsing, URL building and profile storage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.models import AuthorRequest  # noqa: E402
from scholar_crawler.parser import parse_author_page  # noqa: E402
from scholar_crawler.storage import ProfileStore  # noqa: E402
from scholar_crawler.urls import author_url, parse_user_id  # noqa: E402
from tests.fixtures import AUTHOR_LAST_PAGE_HTML, AUTHOR_PAGE_HTML  # noqa: E402

USER = "AAAAAAAAAAAA"


def test_profile_header_fields() -> None:
    profile = parse_author_page(AUTHOR_PAGE_HTML, user_id=USER).profile
    assert profile.user_id == USER
    assert profile.name == "Ada Lovelace"
    assert profile.affiliation == "Professor of Analytical Engines, University of London, Analytical Society"
    assert profile.organization == "University of London"
    assert profile.homepage == "https://example.edu/~ada"
    assert profile.verified_email == "Verified email at example.edu"
    assert profile.interests == ["Computing", "Mathematics"]
    assert profile.fetched_at


def test_summary_table_is_read_by_row_position() -> None:
    profile = parse_author_page(AUTHOR_PAGE_HTML, user_id=USER).profile
    assert (profile.cited_by_total, profile.cited_by_recent) == (12345, 4321)
    assert (profile.h_index, profile.h_index_recent) == (57, 40)
    assert (profile.i10_index, profile.i10_index_recent) == (120, 98)


def test_publications_become_result_records() -> None:
    first, second = parse_author_page(AUTHOR_PAGE_HTML, user_id=USER, cstart=100).results
    assert first.position == 101
    assert first.page_start == 100
    assert first.title == "Notes on the Analytical Engine"
    assert first.link is not None and first.link.startswith("https://scholar.google.com/citations?")
    assert first.authors == "A Lovelace, C Babbage"
    assert first.venue == "Scientific Memoirs 3, 666-731, 1843"
    assert first.year == 1843
    assert first.cited_by_count == 2048
    assert first.cited_by_url == "https://scholar.google.com/scholar?oi=bibs&hl=en&cites=111222333"
    assert first.query == f"author:{USER}"
    assert first.extra["citation_id"] == f"{USER}:u5HHmVD_uO8C"
    assert second.cited_by_count == 0
    assert second.year is None


def test_show_more_button_state_drives_pagination() -> None:
    assert parse_author_page(AUTHOR_PAGE_HTML, user_id=USER).has_more is True
    assert parse_author_page(AUTHOR_LAST_PAGE_HTML, user_id=USER).has_more is False


def test_author_url_paging_and_sorting() -> None:
    url = author_url(AuthorRequest(user_id=USER, language="zh-CN", sort_by_year=True), cstart=200)
    assert url.startswith("https://scholar.google.com/citations?")
    for fragment in (f"user={USER}", "hl=zh-CN", "cstart=200", "pagesize=100", "sortby=pubdate"):
        assert fragment in url
    assert "sortby" not in author_url(AuthorRequest(user_id=USER))


@pytest.mark.parametrize(
    "value",
    [
        USER,
        f"  {USER}  ",
        f"https://scholar.google.com/citations?user={USER}&hl=en",
        f"/citations?hl=en&user={USER}&view_op=list_works",
    ],
)
def test_parse_user_id_accepts_ids_and_urls(value: str) -> None:
    assert parse_user_id(value) == USER


def test_parse_user_id_rejects_unrelated_text() -> None:
    with pytest.raises(ValueError, match="no Scholar profile id"):
        parse_user_id("https://example.edu/~ada")


def test_author_signature_tracks_sort_order() -> None:
    by_citations = AuthorRequest(user_id=USER).signature()
    assert by_citations != AuthorRequest(user_id=USER, sort_by_year=True).signature()


def test_profile_store_keeps_one_record_per_author(tmp_path: Path) -> None:
    path = tmp_path / "out" / "profiles.jsonl"
    store = ProfileStore(path)
    store.load()
    profile = parse_author_page(AUTHOR_PAGE_HTML, user_id=USER).profile
    store.write(profile)
    profile.cited_by_total = 99999
    store.write(profile)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["cited_by_total"] == 99999
    assert store.written == 2

    reloaded = ProfileStore(path)
    reloaded.load()
    other = parse_author_page(AUTHOR_PAGE_HTML, user_id="BBBBBBBBBBBB").profile
    reloaded.write(other)
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 2
