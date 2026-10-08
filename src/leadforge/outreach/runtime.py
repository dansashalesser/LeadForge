"""Build the search service from the config files and the environment.

The one place that reads ``config/outreach.yaml`` (relative to the working directory,
like the ingestion settings) and the Product Catalog (``config/catalog/``, the only
vocabulary source) and turns them into a ``SearchService``. The command line and the
web API both come here, so they cannot differ.
"""

from collections.abc import Mapping

from sqlalchemy import Engine

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.ingest_runner import GLOBAL_MODE_VARIABLE
from leadforge.lead_ingestion.mode_resolution import make_mode_resolver
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.source_settings import load_source_settings
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import Clock, SystemClock
from leadforge.outreach.config import OutreachConfig, load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.service import SearchService

__all__ = ["build_service", "source_mode_notes", "with_usage_overrides"]


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


def source_mode_notes(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """``(source name, resolved mode)`` for each enabled source, before any spend.

    What a run would resolve each source to right now, from the same registry and mode
    resolver the ingestion dashboard uses; the service reports the same fact after the
    run as ``source <name>: <mode>``.
    """
    registry = SourceRegistry.discover(config=load_source_settings())
    resolver = make_mode_resolver(
        environ, global_override=environ.get(GLOBAL_MODE_VARIABLE)
    )
    described = registry.describe(environ, resolve_mode=resolver)
    rows = [d.to_dict() for d in described]
    return [(str(r["name"]), str(r["resolved_mode"])) for r in rows if r["enabled"]]
