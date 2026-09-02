"""Browser-driven Google Scholar crawler with human takeover on verification pages."""

from __future__ import annotations

from .browser import BrowserOptions, browser_session
from .challenge import Challenge, ChallengeKind, ChallengeUnattended, HumanHandoff, detect_challenge
from .crawler import Pacing, ScholarCrawler
from .models import PageResult, ScholarResult, SearchRequest
from .parser import parse_result_page
from .storage import ResultSink, StateStore
from .urls import search_url

__all__ = [
    "BrowserOptions",
    "Challenge",
    "ChallengeKind",
    "ChallengeUnattended",
    "HumanHandoff",
    "PageResult",
    "Pacing",
    "ResultSink",
    "ScholarCrawler",
    "ScholarResult",
    "SearchRequest",
    "StateStore",
    "browser_session",
    "detect_challenge",
    "parse_result_page",
    "search_url",
]
