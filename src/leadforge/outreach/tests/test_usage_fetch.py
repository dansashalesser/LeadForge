"""Page fetch and passages (Req 4.2, 4.3, 9.2)."""

import httpx
import pytest
import respx

from leadforge.outreach.usage.budget import UsageBudget
from leadforge.outreach.usage.fetch import (
    PageFetcher,
    Passages,
    Skipped,
    should_fetch,
)

ROBOTS = "User-agent: *\nDisallow: /private/\n"
PAGE = (
    "<html><script>x=1</script><body><p>"
    + "a " * 100
    + "We use Acme Forge daily. "
    + "b " * 100
    + "</p></body></html>"
)


def make(**kw):
    budget = kw.pop("budget", UsageBudget(searches=1, fetches=5, llm_calls=1))
    return PageFetcher(httpx.Client(), budget, **kw), budget


# Verifies: specs/user-recognition/requirements.md#4.2
@respx.mock
def test_passages_around_alias_and_robots_cached():
    robots = respx.get("https://ex.com/robots.txt").respond(200, text=ROBOTS)
    page = respx.get("https://ex.com/p").respond(200, text=PAGE)
    f, _ = make(passage_chars=30)
    out = f.fetch_passages("https://ex.com/p", ["acme forge"])
    assert isinstance(out, Passages)
    assert len(out.passages) == 1
    assert "Acme Forge" in out.passages[0]
    assert "x=1" not in out.passages[0]
    assert len(out.passages[0]) <= 30 * 2 + len("Acme Forge")
    assert f.fetch_passages("https://ex.com/p", ["acme forge"]) is out
    assert page.call_count == 1
    assert robots.call_count == 1


# Verifies: specs/user-recognition/requirements.md#4.2
@respx.mock
def test_disallowed_path_is_skipped_and_recorded():
    respx.get("https://ex.com/robots.txt").respond(200, text=ROBOTS)
    page = respx.get("https://ex.com/private/x").respond(200, text=PAGE)
    f, _ = make()
    out = f.fetch_passages("https://ex.com/private/x", ["Acme"])
    assert out == Skipped("https://ex.com/private/x", "robots_disallowed")
    assert not page.called
    assert f.skipped == [out]


# Verifies: specs/user-recognition/requirements.md#4.3
@respx.mock
def test_oversize_page_is_skipped():
    respx.get("https://ex.com/robots.txt").respond(404)
    respx.get("https://ex.com/big").respond(200, text="Acme " * 1000)
    f, _ = make(max_bytes=100)
    out = f.fetch_passages("https://ex.com/big", ["Acme"])
    assert out == Skipped("https://ex.com/big", "too_large")


# Verifies: specs/user-recognition/requirements.md#9.2
@respx.mock
@pytest.mark.parametrize(
    "url", ["https://www.linkedin.com/in/x", "https://uk.linkedin.com/company/y"]
)
def test_linkedin_refused_without_any_request(url):
    route = respx.route().respond(200)
    f, budget = make()
    assert f.fetch_passages(url, ["Acme"]) == Skipped(url, "linkedin")
    assert not route.called
    assert budget.used("fetches") == 0


# Verifies: specs/user-recognition/requirements.md#4.2
@respx.mock
def test_no_alias_means_no_fetch():
    route = respx.route().respond(200, text=PAGE)
    f, _ = make()
    assert f.fetch_passages("https://ex.com/p", []) == Skipped(
        "https://ex.com/p", "no_alias"
    )
    assert not route.called
    assert should_fetch("We use Acme Forge", ["acme forge"])
    assert not should_fetch("nothing here", ["acme forge"])
    assert not should_fetch("anything", [])


@respx.mock
def test_page_without_alias_gives_empty_passages_and_budget_exhausted_skips():
    respx.get("https://ex.com/robots.txt").respond(404)
    respx.get("https://ex.com/p").respond(200, text="<p>nothing</p>")
    f, _ = make(budget=UsageBudget(searches=1, fetches=1, llm_calls=1))
    assert f.fetch_passages("https://ex.com/p", ["Acme"]) == Passages(
        "https://ex.com/p", ()
    )
    assert f.fetch_passages("https://ex.com/q", ["Acme"]) == Skipped(
        "https://ex.com/q", "budget_exhausted"
    )


@respx.mock
def test_http_error_is_skipped():
    respx.get("https://ex.com/robots.txt").respond(404)
    respx.get("https://ex.com/p").respond(500)
    f, _ = make()
    assert f.fetch_passages("https://ex.com/p", ["Acme"]) == Skipped(
        "https://ex.com/p", "http_error"
    )
