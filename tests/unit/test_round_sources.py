"""Round-source extraction on small synthetic pages (no third-party content is stored)."""

from datetime import UTC, date, datetime

import httpx
import pytest

from super6.ingestion.round_sources import (
    USER_AGENT,
    PoliteFetcher,
    SourceBlockedError,
    article_links,
    find_round_lines,
    published_at,
    super6_headings,
)

ARTICLE = """
<script>{"datePublished": "2026-11-05T17:40:00+0000"}</script>
<h2>Team A vs Team B, Friday 8pm, live on Sky Sports</h2>
<h2>Team C vs Team D, Saturday 3pm - <a href="https://super6.skysports.com/">PLAY SUPER 6!</a></h2>
<h2>SCORE PREDICTION: 2-1</h2>
<h2>Team E vs Team F, Sunday 4.30pm, live on Sky Sports - <a href="x">PLAY SUPER 6!</a></h2>
<script>var body = "<h2>Team C vs Team D, Saturday 3pm - <a href=\\"https:\\/\\/super6.skysports.com\\/\\">PLAY SUPER 6!<\\/a><\\/h2>";</script>
"""
LISTING = """
<a href="/football/news/111/premier-league-predictions-and-best-bets-team-c-vs-team-d">x</a>
<a href="/football/news/222/some-other-story">y</a>
<a href="https://www.skysports.com/football/news/111/premier-league-predictions-and-best-bets-team-c-vs-team-d">dup</a>
"""


def test_extracts_marked_matches_once_and_strips_broadcast_notes() -> None:
    assert super6_headings(ARTICLE) == ["Team C vs Team D, Saturday 3pm",
                                        "Team E vs Team F, Sunday 4.30pm"]
    assert published_at(ARTICLE) == datetime(2026, 11, 5, 17, 40, tzinfo=UTC)
    assert article_links(LISTING) == [
        "https://www.skysports.com/football/news/111/"
        "premier-league-predictions-and-best-bets-team-c-vs-team-d"]


def _client(routes: dict[str, tuple[int, str]], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = routes.get(str(request.url), (404, ""))
        return httpx.Response(status, text=body)
    return httpx.Client(transport=httpx.MockTransport(handler),
                        headers={"User-Agent": USER_AGENT})


def test_finds_round_lines_politely() -> None:
    seen: list[httpx.Request] = []
    routes = {
        "https://www.skysports.com/robots.txt": (200, "User-agent: *\nDisallow: /api/\n"),
        "https://www.skysports.com/premier-league-news": (200, LISTING),
        "https://www.skysports.com/football/news/111/"
        "premier-league-predictions-and-best-bets-team-c-vs-team-d": (200, ARTICLE),
    }
    fetcher = PoliteFetcher(client=_client(routes, seen), sleep=lambda _: None)
    report = find_round_lines(date(2026, 11, 7), fetcher,
                              listings=["https://www.skysports.com/premier-league-news"])
    assert report.lines == ["Team C vs Team D, Saturday 3pm", "Team E vs Team F, Sunday 4.30pm"]
    assert report.articles[0]["super6_matches"] == 2
    assert all(r.headers["User-Agent"] == USER_AGENT for r in seen)
    # An article from a different week is ignored.
    stale = find_round_lines(date(2026, 12, 5), fetcher,
                             listings=["https://www.skysports.com/premier-league-news"])
    assert stale.lines == [] and "outside the round week" in stale.skipped[0]


def test_robots_disallow_is_respected() -> None:
    seen: list[httpx.Request] = []
    routes = {"https://example.test/robots.txt": (200, "User-agent: *\nDisallow: /news/\n")}
    fetcher = PoliteFetcher(client=_client(routes, seen), sleep=lambda _: None)
    with pytest.raises(SourceBlockedError):
        fetcher.get("https://example.test/news/article")
    assert [str(r.url) for r in seen] == ["https://example.test/robots.txt"]
