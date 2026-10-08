"""Build the search service from the config files and the environment.

The one place that reads ``config/outreach.yaml`` (relative to the working directory,
like the ingestion settings) and the Product Catalog (``config/catalog/``, the only
vocabulary source) and turns them into a ``SearchService``. The command line and the
web API both come here, so they cannot differ.
"""

from collections.abc import Mapping

from sqlalchemy import Engine

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import Clock, SystemClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.service import SearchService

__all__ = ["build_service"]


def build_service(
    environ: Mapping[str, str],
    *,
    clock: Clock | None = None,
    engine: Engine | None = None,
) -> SearchService:
    config = load_outreach_config()
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ=environ,
        clock=clock or SystemClock(),
        acceptance=SeededAcceptance(config.simulation),
        dispatcher=DryRunDispatcher(config.outbox_path),
        engine=engine,
    )
