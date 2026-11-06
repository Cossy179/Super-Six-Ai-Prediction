"""Find a Super 6 round's fixtures in public preview articles (Phase 3, fixture import).

The official round sits behind the player's login, and there is no authorised API. Sky
Sports' public news site, however, publishes weekly predictions articles whose headings
mark each Super 6 match, for example::

    <h2>Arsenal vs Hull, Saturday 3pm - <a href="...">PLAY SUPER 6!</a></h2>

This module finds those articles through Sky's public news listing pages, extracts the marked
headings, and returns them as matchup lines for ``round_import``. It fetches politely:

* it identifies itself honestly (no browser impersonation);
* it checks robots.txt for every URL and skips anything disallowed;
* it waits between requests, with a short timeout and retries only on 429/5xx;
* raw pages are kept locally (``data/raw/round_sources``) with a checksum, for provenance,
  and are never committed or redistributed.

The result is still checked by ``round_import`` (team resolution, six fixtures, deadline =
first kick-off). The round file records the article URL so a person can verify it.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
import urllib.robotparser
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

USER_AGENT = "super6-ai/0.1 (personal research; +https://github.com/Cossy179/Super-Six-Ai-Prediction)"
SKY_LISTINGS = (
    "https://www.skysports.com/premier-league-news",
    "https://www.skysports.com/championship-news",
    "https://www.skysports.com/football/news",
)
_ARTICLE_LINK = re.compile(r'href="(?P<href>(?:https://www\.skysports\.com)?/football/news/\d+/'
                           r'[^"#?]*predictions[^"#?]*)"', re.IGNORECASE)
_H2 = re.compile(r"<h2[^>]*>(?P<body>.*?)</h2>", re.IGNORECASE | re.DOTALL)
_MARKER = re.compile(r"\s*[-–—]\s*play super ?6!?\s*$", re.IGNORECASE)
_BROADCAST = re.compile(r",\s*live on [^,]+$", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_PUBLISHED = re.compile(r'"datePublished"\s*:\s*"(?P<ts>[^"]+)"')


class SourceBlockedError(RuntimeError):
    """robots.txt disallows the URL; it is skipped, never worked around."""


@dataclass
class FetchedPage:
    url: str
    text: str
    retrieved_at: datetime
    checksum: str
    local_path: Path | None


class PoliteFetcher:
    def __init__(self, raw_dir: Path | None = None, min_interval: float = 2.0,
                 client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.raw_dir = raw_dir
        self.min_interval = min_interval
        self.client = client or httpx.Client(follow_redirects=True, timeout=20.0,
                                             headers={"User-Agent": USER_AGENT})
        self._sleep = sleep
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        root = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self.client.get(f"{root}/robots.txt")
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            except httpx.HTTPError:
                parser.parse([])  # robots unreachable: treat as no rules, like most crawlers
            self._robots[root] = parser
        return parser.can_fetch(USER_AGENT, url)

    def get(self, url: str) -> FetchedPage:
        if not self.allowed(url):
            raise SourceBlockedError(f"robots.txt disallows {url}")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        delay = 2.0
        for attempt in range(3):
            self._last = time.monotonic()
            resp = self.client.get(url)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                self._sleep(delay)
                delay *= 2
                continue
            resp.raise_for_status()
            break
        text = resp.text
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        retrieved = datetime.now(UTC)
        path = None
        if self.raw_dir is not None:
            path = self.raw_dir / f"{retrieved:%Y%m%d}" / f"{checksum[:16]}.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            path.with_suffix(".meta.json").write_text(json.dumps(
                {"url": url, "retrieved_at": retrieved.isoformat(), "checksum": checksum,
                 "user_agent": USER_AGENT}, indent=2))
        return FetchedPage(url, text, retrieved, checksum, path)


# --------------------------------------------------------------------------- parsing
def _unescape_json_html(text: str) -> str:
    """Article bodies also appear inside JSON strings with escaped slashes and quotes."""
    return text.replace("\\/", "/").replace('\\"', '"')


def article_links(listing_html: str, base: str = "https://www.skysports.com") -> list[str]:
    seen: dict[str, None] = {}
    for m in _ARTICLE_LINK.finditer(listing_html):
        seen.setdefault(urljoin(base, m.group("href")), None)
    return list(seen)


def super6_headings(article_html: str) -> list[str]:
    """Matchup lines from headings marked "PLAY SUPER 6!", in article order, de-duplicated."""
    out: dict[str, None] = {}
    for m in _H2.finditer(_unescape_json_html(article_html)):
        text = html_lib.unescape(_TAG.sub("", m.group("body"))).strip()
        if not _MARKER.search(text):
            continue
        line = _BROADCAST.sub("", _MARKER.sub("", text)).strip()
        if " vs " in line.lower() or " v " in line.lower():
            out.setdefault(line, None)
    return list(out)


def published_at(article_html: str) -> datetime | None:
    m = _PUBLISHED.search(article_html)
    if not m:
        return None
    ts = m.group("ts")
    ts = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", ts)  # +0000 -> +00:00
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


@dataclass
class SourceReport:
    lines: list[str] = field(default_factory=list)
    articles: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def find_round_lines(round_date: date, fetcher: PoliteFetcher,
                     listings: Iterable[str] = SKY_LISTINGS, max_articles: int = 6
                     ) -> SourceReport:
    """Super 6 matchup lines from preview articles published in the week before the round."""
    report = SourceReport()
    earliest = datetime(round_date.year, round_date.month, round_date.day,
                        tzinfo=UTC) - timedelta(days=6)
    latest = earliest + timedelta(days=9)
    candidates: dict[str, None] = {}
    for listing in listings:
        try:
            page = fetcher.get(listing)
        except (SourceBlockedError, httpx.HTTPError) as exc:
            report.skipped.append(f"{listing}: {exc}")
            continue
        for link in article_links(page.text):
            candidates.setdefault(link, None)
    found: dict[str, None] = {}
    for url in list(candidates)[:max_articles]:
        try:
            page = fetcher.get(url)
        except (SourceBlockedError, httpx.HTTPError) as exc:
            report.skipped.append(f"{url}: {exc}")
            continue
        when = published_at(page.text)
        if when is None or not earliest <= when <= latest:
            report.skipped.append(f"{url}: published {when}, outside the round week")
            continue
        lines = super6_headings(page.text)
        report.articles.append({"url": url, "published_at": when.isoformat(),
                                "super6_matches": len(lines), "checksum": page.checksum,
                                "local_path": str(page.local_path) if page.local_path else None})
        for line in lines:
            found.setdefault(line, None)
    report.lines = list(found)
    return report
