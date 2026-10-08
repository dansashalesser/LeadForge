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
from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.service import SearchService

__all__ = ["build_service", "with_usage_overrides"]


def with_usage_overrides(
    config: OutreachConfig,
    *,
    include_ecosystem: bool | None = None,
    usage_budget: int | None = None,
) -> OutreachConfig:
    """A copy of ``config`` with the one-run usage overrides applied (Req 2.2, 4.5)."""
    if usage_budget is not None and usage_budget < 0:
        raise ValueError(f"--usage-budget must be 0 or more, got {usage_budget}")
    usage = config.usage
    if include_ecosystem is not None:
        usage = usage.model_copy(update={"include_ecosystem": include_ecosystem})
    if usage_budget is not None:
        budget = usage.budget.model_copy(update={"searches": usage_budget})
        usage = usage.model_copy(update={"budget": budget})
    if usage is config.usage:
        return config
    return config.model_copy(update={"usage": usage})


def build_service(
    environ: Mapping[str, str],
    *,
    include_ecosystem: bool | None = None,
    usage_budget: int | None = None,
    clock: Clock | None = None,
    engine: Engine | None = None,
) -> SearchService:
    config = with_usage_overrides(
        load_outreach_config(),
        include_ecosystem=include_ecosystem,
        usage_budget=usage_budget,
    )
    return SearchService(
        config=config,
        catalog=load_catalog(),
        environ=environ,
        clock=clock or SystemClock(),
        acceptance=SeededAcceptance(config.simulation),
        dispatcher=DryRunDispatcher(config.outbox_path),
        engine=engine,
    )
