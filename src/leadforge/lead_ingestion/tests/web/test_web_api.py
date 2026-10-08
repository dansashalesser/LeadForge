"""The web UI's API: a demo run started through it, then every read the UI makes.

Sockets are blocked as in the demo dataset test: the run is synthetic end to end.
"""

import io
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from leadforge.lead_ingestion.match_key_digest import MATCH_KEY_SECRET_ENV
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.tests.socket_guard import guard_for_mode
from leadforge.lead_ingestion.web.api import create_app

DISCOVERED = SourceRegistry.discover()
WRITE = {"X-LeadForge": "1"}
JOB_TIMEOUT_S = 300


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    for name in DISCOVERED.names():
        for variable in DISCOVERED.source_class(name).required_env:
            monkeypatch.delenv(variable, raising=False)
    for variable in ("LEADFORGE_ENV_FILE", MATCH_KEY_SECRET_ENV, "DATABASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("LEADFORGE_MODE", raising=False)
    monkeypatch.chdir(tmp_path)
    blocked = guard_for_mode(DataMode.SYNTHETIC)
    blocked.install(monkeypatch)
    app = create_app(main_url=None, demo_db=tmp_path / "demo" / "demo.db")
    with TestClient(app) as test_client:
        yield test_client
    blocked.assert_clean()


def _wait(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get("/api/jobs/current").json()["job"]
        if job["state"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_a_write_needs_the_header(client: TestClient) -> None:
    response = client.post("/api/jobs", json={"kind": "demo_run"})
    assert response.status_code == 403
    assert client.get("/api/jobs/current").json() == {"job": None}


def test_a_foreign_host_is_refused(client: TestClient) -> None:
    assert (
        client.get("/api/stores", headers={"Host": "evil.example"}).status_code == 400
    )


def test_an_empty_store_says_so(client: TestClient) -> None:
    for path in (
        "/api/main/leads",
        "/api/main/leads.xlsx",
        "/api/main/runs",
        "/api/demo/scorecard",
    ):
        response = client.get(path)
        assert response.status_code == 409, path
    assert client.get("/api/elsewhere/leads").status_code == 404
    assert client.get("/api/elsewhere/leads.xlsx").status_code == 404


def test_the_page_and_sources_are_served(client: TestClient) -> None:
    assert "LeadForge" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    modes = {
        s["name"]: s["resolved_mode"]
        for s in client.get("/api/sources").json()["sources"]
    }
    assert set(modes) == set(DISCOVERED.names())
    assert set(modes.values()) == {"synthetic"}


def test_a_demo_run_through_the_api_is_readable_and_scored(client: TestClient) -> None:
    started = client.post(
        "/api/jobs", json={"kind": "demo_run", "fresh": True}, headers=WRITE
    )
    assert started.status_code == 200
    job = _wait(client)
    assert job["state"] == "succeeded", job
    assert job["exit_code"] == 0
    assert all(name in job["report"] for name in DISCOVERED.names())

    masked = client.get("/api/demo/leads").json()["leads"]
    assert masked
    emails = [x["lead"]["email"] for x in masked if x["lead"]["email"]]
    assert emails
    assert all("***@" in e for e in emails)
    revealed = client.get("/api/demo/leads", params={"reveal": True}).json()["leads"]
    assert not any("***" in (x["lead"]["email"] or "") for x in revealed)

    workbook = client.get("/api/demo/leads.xlsx")
    assert workbook.status_code == 200
    assert "demo-leads.xlsx" in workbook.headers["content-disposition"]
    rows = list(load_workbook(io.BytesIO(workbook.content))["Leads"].values)
    assert rows[0][:3] == ("Lead ID", "Name", "Email")
    assert {r[0] for r in rows[1:]} == {x["lead_id"] for x in masked}
    assert [r[2] for r in rows[1:] if r[2]] == emails

    runs = client.get("/api/demo/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert {s["source_name"] for s in runs[0]["sources"]} == set(DISCOVERED.names())

    card = client.get("/api/demo/scorecard").json()
    assert card["scenarios"]
    assert card["checks_passed"] > card["checks_failed"]
    ids = {x["lead_id"] for x in masked}
    found = [p for p in card["persons"] if p["lead_id"]]
    assert found
    assert {p["lead_id"] for p in found} <= ids
    per_person = sum(
        1 for p in card["persons"] for c in p["checks"] if c["outcome"] is True
    )
    assert per_person == card["checks_passed"]

    one = client.get(f"/api/demo/leads/{found[0]['lead_id']}").json()
    assert one["lead_id"] == found[0]["lead_id"]
    assert one["provenance"]
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/demo/leads/{missing}").status_code == 404
