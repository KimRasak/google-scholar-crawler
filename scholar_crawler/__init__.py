"""Browser-driven Google Scholar crawler with human takeover on verification pages."""

from __future__ import annotations

from .browser import BrowserOptions, browser_session
from .challenge import Challenge, ChallengeKind, ChallengeUnattended, HumanHandoff, detect_challenge
from .crawler import Pacing, ScholarCrawler
from .models import (
    AuthorPage,
    AuthorProfile,
    AuthorRequest,
    PageResult,
    ScholarResult,
    SearchRequest,
)
from .parser import parse_author_page, parse_result_page
from .storage import ProfileStore, ResultSink, StateStore
from .urls import author_url, parse_cluster_id, parse_user_id, search_url

__all__ = [
    "AuthorPage",
    "AuthorProfile",
    "AuthorRequest",
    "BrowserOptions",
    "Challenge",
    "ChallengeKind",
    "ChallengeUnattended",
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
    "browser_session",
    "detect_challenge",
    "parse_author_page",
    "parse_cluster_id",
    "parse_result_page",
    "parse_user_id",
    "search_url",
]
