"""Fetch a page and keep passages around alias mentions (Req 4.2, 4.3, 9.2).

Respects robots.txt, caps the bytes read, never touches linkedin.com. Skips are
recorded (``PageFetcher.skipped``), not raised. Fetched text is untrusted data.
"""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from leadforge.outreach.usage.budget import BUDGET_EXHAUSTED, UsageBudget

__all__ = ["FetchError", "PageFetcher", "Passages", "Skipped", "should_fetch"]

USER_AGENT = "LeadForgeBot/1.0"
DEFAULT_MAX_BYTES = 500_000
DEFAULT_PASSAGE_CHARS = 300
_SKIP_TAGS = {"script", "style", "noscript", "template"}


class FetchError(ValueError):
    """Invalid fetcher configuration."""


@dataclass(frozen=True)
class Passages:
    url: str
    passages: tuple[str, ...]


@dataclass(frozen=True)
class Skipped:
    url: str
    # linkedin|no_alias|robots_disallowed|too_large|http_error|budget_exhausted
    reason: str


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        self._skip += tag in _SKIP_TAGS

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _is_linkedin(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def should_fetch(snippet: str, aliases: list[str]) -> bool:
    """A result is fetched only when its snippet names an alias (Req 4.2)."""
    low = snippet.lower()
    return any(a and a.lower() in low for a in aliases)


class PageFetcher:
    """One per run: caches robots files and page results."""

    def __init__(
        self,
        client: httpx.Client,
        budget: UsageBudget,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        passage_chars: int = DEFAULT_PASSAGE_CHARS,
    ) -> None:
        if max_bytes <= 0 or passage_chars <= 0:
            raise FetchError("max_bytes and passage_chars must be > 0")
        self._client, self._budget = client, budget
        self._max_bytes, self._chars = max_bytes, passage_chars
        self._robots: dict[str, RobotFileParser | None] = {}
        self._cache: dict[str, Passages | Skipped] = {}
        self.skipped: list[Skipped] = []

    def _skip(self, url: str, reason: str) -> Skipped:
        out = Skipped(url, reason)
        self.skipped.append(out)
        self._cache[url] = out
        return out

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """None means no restrictions; an unreadable file blocks everything."""
        parser = RobotFileParser()
        try:
            resp = self._client.get(
                f"{origin}/robots.txt", headers={"User-Agent": USER_AGENT}
            )
        except httpx.HTTPError:
            parser.disallow_all = True
            return parser
        if resp.status_code == 200:
            parser.parse(resp.text.splitlines())
        elif resp.status_code in (401, 403) or resp.status_code >= 500:
            parser.disallow_all = True
        else:
            return None
        return parser

    def _read(self, url: str) -> str | Skipped:
        try:
            with self._client.stream(
                "GET", url, headers={"User-Agent": USER_AGENT}
            ) as resp:
                if resp.status_code != 200:
                    return self._skip(url, "http_error")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        return self._skip(url, "too_large")
                    chunks.append(chunk)
        except httpx.HTTPError:
            return self._skip(url, "http_error")
        return b"".join(chunks).decode("utf-8", errors="replace")

    def fetch_passages(self, url: str, aliases: list[str]) -> Passages | Skipped:
        if url in self._cache:
            return self._cache[url]
        parts = urlsplit(url)
        if _is_linkedin(parts.hostname or ""):
            return self._skip(url, "linkedin")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return self._skip(url, "http_error")  # no host to judge, no request
        names = [a for a in aliases if a]
        if not names:
            return self._skip(url, "no_alias")
        if not self._allowed(url):
            return self._skip(url, "robots_disallowed")
        if not self._budget.spend("fetches"):
            return self._skip(url, BUDGET_EXHAUSTED)
        body = self._read(url)
        if isinstance(body, Skipped):
            return body
        out = Passages(url, self._windows(html_to_text(body), names))
        self._cache[url] = out
        return out

    def _windows(self, text: str, names: list[str]) -> tuple[str, ...]:
        low, spans = text.lower(), []
        for name in names:
            for m in re.finditer(re.escape(name.lower()), low):
                lo, hi = (
                    max(0, m.start() - self._chars),
                    min(len(text), m.end() + self._chars),
                )
                if spans and lo <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(hi, spans[-1][1]))
                else:
                    spans.append((lo, hi))
            spans.sort()
        return tuple(text[lo:hi] for lo, hi in spans)
