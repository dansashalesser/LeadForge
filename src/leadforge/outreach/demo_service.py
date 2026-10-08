"""The search service over the synthetic demo dataset, for the web UI and the CLI.

Same wiring as the end-to-end demo tests: ingestion is served by the demo transport
(40 companies, 250 people, no socket), the invite answers come from the demo answer
key, sends are dry runs and the store is the demo database the dashboard already uses
(``.leadforge/demo.db``), never the operator's own. ``.env`` keys are never used: the
service gets an empty environment and the run forces ``LEADFORGE_MODE=synthetic``.

``run_ingestion`` reads its store and mode from the process environment, so a demo
search sets those for its own duration under one lock (the same trick the dashboard's
demo job uses); a store read of the operator's own store waits on the same lock, so it
never sees the demo's value.
"""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.database import create_store_engine, local_file_url
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import DEMO_RETRY
from leadforge.lead_ingestion.demo.transport import demo_transport_factory
from leadforge.lead_ingestion.ingest_runner import GLOBAL_MODE_VARIABLE, run_ingestion
from leadforge.outreach.acceptance import AnswerKeyAcceptance, SeededAcceptance
from leadforge.outreach.clock import SystemClock
from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.runtime import with_usage_overrides
from leadforge.outreach.service import SearchService

__all__ = [
    "DEMO_OUTBOX",
    "ENV_LOCK",
    "build_demo_service",
    "demo_acceptance",
    "demo_search",
]

DEMO_OUTBOX = "outbox.jsonl"
_DATABASE_URL = "DATABASE_URL"

# Held while the process environment names the demo store; re-entrant so a demo search
# may call helpers that take it again.
ENV_LOCK = threading.RLock()


@contextmanager
def _demo_environment(url: str) -> Iterator[None]:
    names = (_DATABASE_URL, GLOBAL_MODE_VARIABLE)
    with ENV_LOCK:
        saved = {name: os.environ.get(name) for name in names}
        os.environ[_DATABASE_URL] = url
        os.environ[GLOBAL_MODE_VARIABLE] = "synthetic"
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def demo_acceptance(engine: Engine, config: OutreachConfig) -> AnswerKeyAcceptance:
    """Invite answers from the demo answer key; unknown Leads follow the seeded rule."""
    return AnswerKeyAcceptance(
        generator.load(generator.ANSWER_KEY),
        partial(Session, engine),
        SeededAcceptance(config.simulation),
    )


def build_demo_service(
    engine: Engine,
    demo_db: Path,
    *,
    include_ecosystem: bool | None = None,
    usage_budget: int | None = None,
) -> SearchService:
    """A ``SearchService`` over the demo dataset, storing into ``engine``."""
    config = with_usage_overrides(
        load_outreach_config(),
        include_ecosystem=include_ecosystem,
        usage_budget=usage_budget,
    )
    factory, _ = demo_transport_factory()
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ={},
        clock=SystemClock(),
        acceptance=demo_acceptance(engine, config),
        dispatcher=DryRunDispatcher(demo_db.parent / DEMO_OUTBOX, lambda _: None),
        engine=engine,
        ingest=partial(
            run_ingestion, transport_factory=factory, synthetic_retry=DEMO_RETRY
        ),
    )


@contextmanager
def demo_search(
    demo_db: Path,
    *,
    include_ecosystem: bool | None = None,
    usage_budget: int | None = None,
) -> Iterator[SearchService]:
    """Yield a demo service while the process environment names the demo store.

    The demo database (and its directory) is created by the search itself; the service
    migrates it to head before the first write, so a fresh demo needs no prior run.
    """
    db = demo_db.resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_store_engine(local_file_url(db))
    try:
        with _demo_environment(local_file_url(db)):
            yield build_demo_service(
                engine,
                db,
                include_ecosystem=include_ecosystem,
                usage_budget=usage_budget,
            )
    finally:
        engine.dispose()
