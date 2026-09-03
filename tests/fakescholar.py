"""A local stand-in for Scholar, served over HTTP for end-to-end tests.

The unit tests feed HTML strings straight to the parser, and ``--self-check`` needs the real
Scholar. Neither exercises the path that matters most: a real browser navigating real URLs,
tripping a challenge, resuming after a takeover and writing files. This server closes that
gap without sending anything to Google.

It answers the two paths the crawler builds URLs for — ``/scholar`` and ``/citations`` — and
can be told to answer a given offset with a challenge page the first time it is asked, which
is what a human then clears by reloading.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from tests.fixtures import AUTHOR_PAGE_HTML, CAPTCHA_PAGE_HTML, EMPTY_PAGE_HTML, result_page_html

PAGE_SIZE = 10
"""Result cards per served page, matching Scholar's default."""


class FakeScholar:
    """Serves result pages, an author profile and challenges, and records what was asked.

    :param pages: how many result pages exist; the last one advertises no successor.
    :param challenge_at: offsets answered with a challenge page the first time they are asked.
    :param status: HTTP status every response carries.
    :param body: HTML served instead of a result page, for a site that answers with something
        this tool cannot read.
    """

    def __init__(
        self,
        *,
        pages: int = 2,
        challenge_at: tuple[int, ...] = (),
        status: int = 200,
        body: str | None = None,
        unreadable_for: tuple[str, ...] = (),
    ) -> None:
        """Build a site.

        :param pages: how many result pages exist.
        :param challenge_at: offsets answered with a challenge page the first time.
        :param status: HTTP status every response carries.
        :param body: HTML served instead of the result pages.
        :param unreadable_for: queries answered with a page carrying no Scholar markers, so a
            batch can fail on one target while the others succeed.
        """
        self.pages = pages
        self.challenge_at = set(challenge_at)
        self.status = status
        self.body = body
        self.unreadable_for = set(unreadable_for)
        self.requests: list[str] = []
        self.challenges_served: list[int] = []
        self._served_challenge: set[int] = set()
        self._lock = threading.Lock()

    def body_for(self, path: str, query: dict[str, list[str]]) -> str:
        """Choose the page for one request.

        :param path: requested path.
        :param query: parsed query parameters.
        :returns: the HTML to serve.
        """
        with self._lock:
            self.requests.append(f"{path}?{'&'.join(f'{k}={v[0]}' for k, v in sorted(query.items()))}")
            if self.body is not None:
                return self.body
            if path == "/citations":
                return AUTHOR_PAGE_HTML
            if query.get("q", [""])[0] in self.unreadable_for:
                return "<html><body><p>nothing this tool can read</p></body></html>"
            start = int(query.get("start", ["0"])[0])
            if start in self.challenge_at and start not in self._served_challenge:
                self._served_challenge.add(start)
                self.challenges_served.append(start)
                return CAPTCHA_PAGE_HTML
            if start >= self.pages * PAGE_SIZE:
                return EMPTY_PAGE_HTML
            last = start + PAGE_SIZE >= self.pages * PAGE_SIZE
            return result_page_html(
                PAGE_SIZE,
                next_start=None if last else start + PAGE_SIZE,
                first_index=start,
            )

    def offsets_requested(self) -> list[int]:
        """List the result offsets that were asked for, in order.

        :returns: one offset per ``/scholar`` request.
        """
        offsets = []
        for request in self.requests:
            if not request.startswith("/scholar"):
                continue
            found = [part for part in request.split("&") if part.startswith("start=")]
            offsets.append(int(found[0].removeprefix("start=")) if found else 0)
        return offsets


class _Handler(BaseHTTPRequestHandler):
    """Serves one FakeScholar instance; every response is HTML."""

    site: FakeScholar

    def do_GET(self) -> None:  # noqa: N802 — the name is BaseHTTPRequestHandler's
        """Answer one GET with the page the site chooses."""
        parts = urlsplit(self.path)
        body = self.site.body_for(parts.path, parse_qs(parts.query)).encode("utf-8")
        self.send_response(self.site.status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — signature is fixed
        """Discard the default stderr access log."""


@contextmanager
def serving(site: FakeScholar) -> Iterator[str]:
    """Run ``site`` on a loopback port for the duration of the block.

    :param site: the fake Scholar to serve.
    :returns: the host URL to pass to the crawler, e.g. ``http://127.0.0.1:54321``.
    """
    handler = type("_BoundHandler", (_Handler,), {"site": site})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
