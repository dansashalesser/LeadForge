"""The outreach API and page, on the ingestion web app (13.1-13.5)."""

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.web.api import create_app
from leadforge.outreach.web import create_router

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
WRITE = {"X-LeadForge": "1"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    discovered = SourceRegistry.discover()
    for source in discovered.names():
        for variable in discovered.source_class(source).required_env:
            monkeypatch.delenv(variable, raising=False)
    for name in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", "LEADFORGE_MATCH_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    url = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    app = create_app(
        main_url=url, demo_db=tmp_path / "demo.db", extra_routers=[create_router()]
    )
    return TestClient(app)


def _plan(
    client: TestClient, mode: str = "users", query: str = "mongodb"
) -> dict[str, object]:
    answer = client.post(
        "/api/outreach/plan", json={"mode": mode, "query": query}, headers=WRITE
    )
    assert answer.status_code == 200, answer.text
    plan = answer.json()["plan"]
    assert isinstance(plan, dict)
    return plan


def _run(client: TestClient) -> str:
    started = client.post(
        "/api/outreach/searches", json={"plan": _plan(client)}, headers=WRITE
    )
    assert started.status_code == 200, started.text
    return str(started.json()["search_id"])


# Verifies: outreach requirements 13.4
@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/outreach/plan", {"mode": "users", "query": "x"}),
        ("/api/outreach/searches", {"plan": {}}),
        ("/api/outreach/tick", {}),
    ],
)
def test_every_write_is_refused_without_the_header(
    client: TestClient, path: str, body: dict[str, object]
) -> None:
    assert client.post(path, json=body).status_code == 403


# Verifies: outreach requirements 1.4
def test_a_plan_is_compiled_without_storing_or_spending(client: TestClient) -> None:
    plan = _plan(client, "free_text", "teams running mongodb")

    assert plan["compiler"] == "offline"
    assert plan["terms"] == ["mongodb"]
    assert client.get("/api/outreach/searches").status_code == 409  # no store yet


# Verifies: outreach requirements 1.1
# Verifies: outreach requirements 3.3
def test_plan_errors_are_named_and_returned_as_400(client: TestClient) -> None:
    bad_mode = client.post(
        "/api/outreach/plan", json={"mode": "everyone", "query": "x"}, headers=WRITE
    )
    no_domain = client.post(
        "/api/outreach/plan",
        json={"mode": "workers", "query": "Nowhere"},
        headers=WRITE,
    )

    assert bad_mode.status_code == no_domain.status_code == 400
    assert "unknown search mode" in bad_mode.json()["detail"]
    assert "no known domain" in no_domain.json()["detail"]


# Verifies: outreach requirements 13.4
def test_a_search_runs_lists_and_shows_detail_masked_unless_revealed(
    client: TestClient,
) -> None:
    search_id = _run(client)

    listed = client.get("/api/outreach/searches").json()["searches"]
    masked = client.get(f"/api/outreach/searches/{search_id}").json()["report"]
    shown = client.get(f"/api/outreach/searches/{search_id}?reveal=true").json()[
        "report"
    ]

    assert [s["search_id"] for s in listed] == [search_id]
    assert listed[0]["funnel"] == masked["funnel"]
    assert masked["leads"]
    assert all("***" in (r["email"] or "***") for r in masked["leads"])
    assert all(r["name"] is None for r in masked["leads"])
    assert any(r["name"] for r in shown["leads"])
    assert (
        client.get(
            f"/api/outreach/searches/{'0' * 8}-0000-0000-0000-{'0' * 12}"
        ).status_code
        == 404
    )


# Verifies: outreach requirements 13.5
# Verifies: outreach requirements 11.3
def test_the_markdown_download_matches_the_cli_report_and_the_json(
    client: TestClient,
) -> None:
    search_id = _run(client)

    md = client.get(f"/api/outreach/searches/{search_id}/report?format=md")
    js = client.get(f"/api/outreach/searches/{search_id}/report?format=json")

    assert md.headers["content-type"].startswith("text/markdown")
    assert js.headers["content-type"].startswith("application/json")
    for name, count in js.json()["funnel"].items():
        assert f"| {name} | {count} |" in md.text
    assert "## What ran" in md.text
    assert (
        client.get(f"/api/outreach/searches/{search_id}/report?format=pdf").status_code
        == 422
    )


# Verifies: outreach requirements 13.3
def test_advancing_days_fires_what_became_due(client: TestClient) -> None:
    search_id = _run(client)

    early = client.post(
        "/api/outreach/tick",
        json={"search_id": search_id, "advance_days": 0},
        headers=WRITE,
    )
    late = client.post(
        "/api/outreach/tick",
        json={"search_id": search_id, "advance_days": 30},
        headers=WRITE,
    )
    both = client.post(
        "/api/outreach/tick",
        json={"advance_days": 1, "now": "2030-01-01T00:00:00Z"},
        headers=WRITE,
    )
    backwards = client.post(
        "/api/outreach/tick", json={"advance_days": -1}, headers=WRITE
    )

    assert early.status_code == late.status_code == 200
    assert early.json()["fired"] == 0
    assert both.status_code == backwards.status_code == 400


# Verifies: outreach requirements 13.1
# Verifies: outreach requirements 13.2
def test_the_page_has_the_search_panel_leads_timeline_and_download(
    client: TestClient,
) -> None:
    page = client.get("/outreach")
    script = client.get("/outreach/app.js")
    styles = client.get("/outreach/app.css")

    assert page.status_code == script.status_code == styles.status_code == 200
    for needle in (
        'id="mode"',
        'id="preview"',
        'id="run"',
        'id="plan"',
        'id="advance"',
        'id="download"',
    ):
        assert needle in page.text
    for mode in ("free_text", "workers", "users"):
        assert f'data-mode="{mode}"' in page.text
    assert "textContent" in script.text
    assert not re.search(r"innerHTML\s*=", script.text)
    assert "X-LeadForge" in script.text


# Verifies: outreach requirements 13.4
def test_the_ingestion_routes_still_work_beside_the_outreach_router(
    client: TestClient,
) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/api/stores").status_code == 200
