"""Search on the dashboard's Demo dataset and on My store (the store selector).

The demo store runs the 40-company, 250-person synthetic dataset exactly as the
end-to-end demo tests do, into its own database; the main store is untouched.
"""

# ruff: noqa: F811 - the `client` fixture is imported from the web tests

import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from leadforge.cli import app
from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.outreach.tests.test_web import WRITE, _plan, _run, client  # noqa: F401

DATASET_VENDOR = "DataStax"
runner = CliRunner()


def _vendor(client: TestClient) -> dict[str, Any]:
    vendors = client.get("/api/catalog").json()["vendors"]
    return next(v for v in vendors if v["name"] == DATASET_VENDOR)


def _demo_search(client: TestClient) -> dict[str, Any]:
    vendor = _vendor(client)
    products = [p["key"] for p in vendor["products"]]
    body = {
        "mode": "users",
        "query": " ".join(products),
        "vendor": vendor["key"],
        "products": products,
        "store": "demo",
    }
    planned = client.post("/api/outreach/plan", json=body, headers=WRITE)
    assert planned.status_code == 200, planned.text
    started = client.post(
        "/api/outreach/searches",
        json={"plan": planned.json()["plan"], "store": "demo"},
        headers=WRITE,
    )
    assert started.status_code == 200, started.text
    return started.json()


@pytest.fixture
def demo_done(client: TestClient) -> dict[str, Any]:
    return _demo_search(client)


# Verifies: the demo store drives search (requirement 2)
def test_a_demo_users_search_selects_the_three_verified_user_companies_people(
    client: TestClient, demo_done: dict[str, Any]
) -> None:
    detail = client.get(
        f"/api/outreach/searches/{demo_done['search_id']}?store=demo"
    ).json()["report"]
    selected = [lead for lead in detail["leads"] if lead["status"] == "selected"]

    assert demo_done["store"] == "demo"
    assert demo_done["counts"]["selected"] == 6
    assert len(selected) == 6
    for lead in selected:
        assert lead["verdict"]
        assert lead["company_usage"]
        assert lead["person_fit"]
        assert lead["evidence"], lead
        assert all(item["url"] and item["quote"] for item in lead["evidence"])
    rejected = [lead for lead in detail["leads"] if lead["status"] == "rejected"]
    assert rejected
    assert "synthetic" in " ".join(demo_done["notes"])


# Verifies: searches list/detail read from the right store (requirement 2)
def test_a_demo_search_is_listed_under_demo_and_a_main_search_under_main(
    client: TestClient, demo_done: dict[str, Any], tmp_path: Path
) -> None:
    before = os.environ["DATABASE_URL"]
    assert client.get("/api/outreach/searches?store=main").status_code == 409

    _plan(client)
    main_id = _run(client)
    demo = client.get("/api/outreach/searches?store=demo").json()["searches"]
    main = client.get("/api/outreach/searches?store=main").json()["searches"]

    assert [s["search_id"] for s in demo] == [demo_done["search_id"]]
    assert [s["search_id"] for s in main] == [main_id]
    # Each store answers only for its own searches.
    assert client.get(f"/api/outreach/searches/{main_id}?store=demo").status_code == 404
    assert (
        client.get(
            f"/api/outreach/searches/{demo_done['search_id']}?store=main"
        ).status_code
        == 404
    )
    report = client.get(
        f"/api/outreach/searches/{demo_done['search_id']}/report?store=demo"
    )
    assert report.status_code == 200
    assert (tmp_path / ".leadforge" / "demo.db").exists()
    # The demo search borrowed the process environment and put it back.
    assert os.environ["DATABASE_URL"] == before
    assert "LEADFORGE_MODE" not in os.environ


# Verifies: a fresh demo store needs no earlier run (requirement 3)
def test_the_demo_store_reads_as_empty_until_its_first_search(
    client: TestClient, tmp_path: Path
) -> None:
    answer = client.get("/api/outreach/searches?store=demo")

    assert answer.status_code == 409
    assert "demo" in answer.json()["detail"]
    assert not (tmp_path / ".leadforge" / "demo.db").exists()


# Verifies: the plan step shows which sources are live (requirement 2)
def test_a_plan_reports_the_source_modes_of_the_store(
    client: TestClient,
) -> None:
    body = {"mode": "free_text", "query": "mongodb", "store": "main"}
    main = client.post("/api/outreach/plan", json=body, headers=WRITE).json()
    demo = client.post(
        "/api/outreach/plan", json={**body, "store": "demo"}, headers=WRITE
    ).json()

    assert main["store"] == "main"
    assert all(re.fullmatch(r"source \S+: \w+", n) for n in main["notes"])
    assert main["live_sources"] == []  # no keys in this environment
    assert demo["live_sources"] == []
    assert "synthetic" in demo["notes"][0]


def test_an_unknown_store_is_refused(client: TestClient) -> None:
    assert client.get("/api/outreach/searches?store=other").status_code == 422


# Verifies: Search is the dashboard's first tab; the old page still resolves
def test_the_dashboard_serves_the_search_tab_and_the_old_url_redirects(
    client: TestClient,
) -> None:
    page = client.get("/").text
    old = client.get("/outreach", follow_redirects=False)
    followed = client.get("/outreach")

    assert page.index("/outreach/app.js") < page.index("/static/app.js")
    assert 'data-tab="overview"' in page
    assert old.status_code in (302, 307)
    assert old.headers["location"] == "/?tab=search"
    assert followed.status_code == 200
    script = client.get("/static/app.js").text
    assert "leadforgeTabs" in script
    assert "outreach" not in script.lower()


# Verifies: `outreach search ... --demo` uses the same wiring (requirement 2)
def test_the_cli_demo_flag_searches_the_demo_dataset_into_the_demo_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Path(__file__).resolve().parents[5] / "config"
    shutil.copytree(config, tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    for name in ("LEADFORGE_MODE", "LEADFORGE_ENV_FILE", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'store.db'}")

    vendor = next(v for v in load_catalog().vendors() if v.name == DATASET_VENDOR)
    command = ["outreach", "search", "users", "--vendor", vendor.key, "--demo"]
    for product in vendor.products:
        command += ["--product", product.key]
    done = runner.invoke(app, command)

    assert done.exit_code == 0, done.output
    assert "selected: 6" in done.output
    assert (tmp_path / ".leadforge" / "demo.db").exists()
    assert not (tmp_path / "store.db").exists()
