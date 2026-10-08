"""The usage stage in the search service: users mode only, before qualify (4, 8)."""

# ruff: noqa: F811 - fixtures imported from the ingestion tests

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.ingest_runner import run_ingestion
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.tests.test_persistence_both_engines import (  # noqa: F401 - fixtures
    Backend,
    backend,
    blank,
    postgres_url,
)
from leadforge.lead_ingestion.tests.test_run_lifecycle_both_engines import (  # noqa: F401 - fixtures
    composed,
)
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import FakeClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import UsageClassifierUnavailableError
from leadforge.outreach.search_plan import parse_request
from leadforge.outreach.service import SearchService, no_employer_verdict
from leadforge.outreach.tables import OutreachDecision, OutreachSearch
from leadforge.outreach.tests.support import NOW
from leadforge.outreach.usage.classify import OfflineClassifier, UsageCues

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"


class _Serp:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, spec: Any) -> list[Any]:
        self.calls += 1
        return []


class _Pages:
    def fetch_passages(self, url: str, aliases: list[str]) -> Any:
        raise AssertionError("no result, so nothing to fetch")


class _Deps:
    def __init__(self) -> None:
        self.serp = _Serp()
        self.budgets: list[Any] = []

    def __call__(self, budget: Any) -> tuple[Any, Any]:
        self.budgets.append(budget)
        return self.serp, _Pages()


def _service(
    backend: Backend, tmp_path: Path, deps: _Deps, **extra: Any
) -> SearchService:
    config = load_outreach_config(CONFIG_DIR / "outreach.yaml")
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ={},
        clock=FakeClock(NOW),
        acceptance=SeededAcceptance(config.simulation),
        dispatcher=DryRunDispatcher(tmp_path / "outbox.jsonl", lambda _: None),
        engine=backend.engine,
        usage_deps=deps,
        **extra,
    )


# Verifies: specs/user-recognition/requirements.md#4.5
# Verifies: specs/user-recognition/requirements.md#8.1
async def test_a_users_search_runs_the_stage_with_the_configured_budget(
    composed: Backend, tmp_path: Path
) -> None:
    deps = _Deps()
    service = _service(composed, tmp_path, deps)
    plan = service.plan(parse_request("users", "mongodb", products=["mongodb"]))

    summary = await service.run(plan, show=lambda _: None)

    assert summary.gathered > 0
    assert len(deps.budgets) == 1
    assert (deps.budgets[0].searches, deps.budgets[0].fetches) == (200, 300)
    assert deps.serp.calls > 0
    with Session(composed.engine) as session:
        n = session.scalar(sa.select(sa.func.count()).select_from(OutreachDecision))
    assert n == summary.gathered


# Verifies: specs/user-recognition/requirements.md#8.1
async def test_workers_and_free_text_searches_never_touch_the_stage(
    composed: Backend, tmp_path: Path
) -> None:
    deps = _Deps()
    service = _service(composed, tmp_path, deps)
    plan = service.plan(parse_request("free_text", "teams running mongodb"))

    await service.run(plan, show=lambda _: None)

    assert deps.budgets == []


# Verifies: specs/user-recognition/requirements.md#4.4
async def test_a_live_flow_without_a_classifier_fails_before_any_search(
    composed: Backend, tmp_path: Path
) -> None:
    deps = _Deps()

    async def live(**kw: Any) -> Any:
        out = await run_ingestion(**kw)
        results = tuple(
            SimpleNamespace(source_name=r.source_name, resolved_mode=DataMode.LIVE)
            for r in out.results
        )
        return SimpleNamespace(run_id=out.run_id, results=results)

    service = _service(composed, tmp_path, deps, ingest=live)
    plan = service.plan(parse_request("users", "mongodb", products=["mongodb"]))

    with pytest.raises(UsageClassifierUnavailableError):
        await service.run(plan, show=lambda _: None)

    assert deps.serp.calls == 0
    with Session(composed.engine) as session:
        status = session.scalars(sa.select(OutreachSearch.status)).one()
    assert status == "failed"


# Verifies: specs/user-recognition/requirements.md#4.4
async def test_an_injected_classifier_is_used_in_the_live_flow(
    composed: Backend, tmp_path: Path
) -> None:
    deps = _Deps()

    async def live(**kw: Any) -> Any:
        out = await run_ingestion(**kw)
        results = tuple(
            SimpleNamespace(source_name=r.source_name, resolved_mode=DataMode.LIVE)
            for r in out.results
        )
        return SimpleNamespace(run_id=out.run_id, results=results)

    service = _service(
        composed,
        tmp_path,
        deps,
        ingest=live,
        usage_classifier=OfflineClassifier(UsageCues()),
    )
    plan = service.plan(parse_request("users", "mongodb", products=["mongodb"]))

    summary = await service.run(plan, show=lambda _: None)

    assert summary.gathered > 0


# Verifies: specs/user-recognition/requirements.md#8.1
def test_a_lead_with_no_employer_is_rejected_not_missing() -> None:
    verdict = no_employer_verdict()

    assert verdict.status.value == "rejected"
    assert verdict.reason == "no_employer"
    assert verdict.evidence_refs == ()
