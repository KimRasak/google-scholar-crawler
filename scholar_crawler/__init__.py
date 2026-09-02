"""Browser-driven Google Scholar crawler with human takeover on verification pages."""

from __future__ import annotations

from .browser import BrowserOptions, browser_session
from .challenge import Challenge, ChallengeKind, ChallengeUnattended, HumanHandoff, detect_challenge
from .crawler import Pacing, ScholarCrawler
from .expand import FollowPolicy, next_level
from .models import (
    AuthorPage,
    AuthorProfile,
    AuthorRequest,
    PageResult,
    ScholarResult,
    SearchRequest,
)
from .parser import bibtex_key, bibtex_link, parse_author_page, parse_bibtex, parse_result_page
from .storage import BibtexSink, ProfileStore, ResultSink, StateStore
from .urls import author_url, cite_url, parse_cluster_id, parse_user_id, search_url

__all__ = [
    "AuthorPage",
    "AuthorProfile",
    "AuthorRequest",
    "BibtexSink",
    "BrowserOptions",
    "Challenge",
    "ChallengeKind",
    "ChallengeUnattended",
    "FollowPolicy",
    "HumanHandoff",
    "Pacing",
    "PageResult",
    "ProfileStore",
    "ResultSink",
    "ScholarCrawler",
    "ScholarResult",
    "SearchRequest",
    "StateStore",
    "author_url",
    "bibtex_key",
    "bibtex_link",
    "browser_session",
    "cite_url",
    "detect_challenge",
    "next_level",
    "parse_author_page",
    "parse_bibtex",
    "parse_cluster_id",
    "parse_result_page",
    "parse_user_id",
    "search_url",
]
