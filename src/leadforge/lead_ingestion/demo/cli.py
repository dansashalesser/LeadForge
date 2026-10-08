"""``leadforge demo``: generate the demo tables, run the pipeline on them, score it.

``run`` always runs every source synthetic (``LEADFORGE_MODE=synthetic`` is forced, so
keys in ``.env`` never send the demo to a provider) and stores into its own SQLite
file, ``.leadforge/demo.db``, unless ``--database-url`` names another store; the
operator's own leads are never mixed with it. The requests the demo transport served
are written beside the database (``demo-run.json``) for ``score`` to read.
Read the demo leads with ``DATABASE_URL=sqlite:///.leadforge/demo.db leadforge leads
list``.
"""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.database import (
    DatabaseConfigError,
    create_store_engine,
    local_file_url,
)
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.scorecard import load_active_leads, render, score
from leadforge.lead_ingestion.demo.transport import demo_transport_factory
from leadforge.lead_ingestion.env_file import EnvFileError, load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError, run_ingestion
from leadforge.lead_ingestion.log_redaction import configure_logging
from leadforge.lead_ingestion.retry import RetryPolicy
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.lead_ingestion.target_profile import TargetProfile

EXIT_CONFIGURATION_ERROR = 2
EXIT_RUN_IN_PROGRESS = 3
DEFAULT_DB = Path(".leadforge") / "demo.db"
# Superseded by ``demo_profile`` (removed with the file in task 3.5).
PROFILE = Path(__file__).parent / "target_profile.yaml"
_KEYWORD_TEMPLATES = (
    "{term} migration",
    "hiring {term} engineer",
    "{term} alternative",
)


def demo_profile() -> TargetProfile:
    """The demo's Target Profile, built from the catalog: DataStax is the target,
    every other vendor a competitor, Apollo takes UIDs and Google phrases."""
    catalog = load_catalog()
    built = catalog.to_profile(
        "datastax",
        competitors=tuple(k for k in catalog.vendor_keys() if k != "datastax"),
        uid_source="apollo",
        alias_source="google_search",
    )
    return replace(built, keyword_templates=_KEYWORD_TEMPLATES)


# The demo serves one Apollo 429: retry it as a live run would, with millisecond
# backoff so the demo never waits.
DEMO_RETRY = RetryPolicy(base_delay_s=0.001, max_delay_s=0.01, max_retry_after_s=0.01)
RUN_LOG = "demo-run.json"

demo_app = typer.Typer(
    no_args_is_help=True, help="Demo dataset: generate, run and score."
)

_DB_URL = typer.Option(
    "--database-url", help="Store for the demo leads (default .leadforge/demo.db)."
)


def _use_demo_store(database_url: str | None) -> Path:
    """Point this process at the demo store; return the run-log path beside it."""
    DEFAULT_DB.parent.mkdir(parents=True, exist_ok=True)
    if database_url:
        os.environ["DATABASE_URL"] = database_url
        return DEFAULT_DB.parent / RUN_LOG
    os.environ["DATABASE_URL"] = local_file_url(DEFAULT_DB)
    return DEFAULT_DB.parent / RUN_LOG


@demo_app.command("generate")
def generate() -> None:
    """Rewrite the four provider tables and the answer key from the fixed seed."""
    for path in generator.write():
        typer.echo(str(path))


@demo_app.command("run")
def run(
    database_url: Annotated[str | None, _DB_URL] = None,
    faults: Annotated[
        bool, typer.Option("--faults", help="Also serve the scripted SerpApi failure.")
    ] = False,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Delete .leadforge/demo.db first.")
    ] = False,
) -> None:
    """Run ingestion on the demo tables, every source synthetic."""
    if fresh and database_url is None:
        DEFAULT_DB.unlink(missing_ok=True)
    run_log = _use_demo_store(database_url)
    os.environ["LEADFORGE_MODE"] = "synthetic"
    factory, log = demo_transport_factory(faults=faults)
    try:
        load_env_file_into_process()
        configure_logging(os.environ)
        outcome = asyncio.run(
            run_ingestion(
                target_profile=demo_profile(),
                transport_factory=factory,
                synthetic_retry=DEMO_RETRY,
            )
        )
    except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    except RunInProgressError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(EXIT_RUN_IN_PROGRESS) from None
    run_log.write_text(json.dumps(log.as_json(), indent=1) + "\n", encoding="utf-8")
    typer.echo(outcome.exit.summary)
    typer.echo("")
    typer.echo(outcome.report_text)
    raise typer.Exit(outcome.exit.exit_code)


@demo_app.command("score")
def score_(database_url: Annotated[str | None, _DB_URL] = None) -> None:
    """Compare the stored demo leads with the answer key."""
    run_log = _use_demo_store(database_url)
    try:
        load_env_file_into_process()
        engine = create_store_engine()
        require_head(engine)
    except (
        ConfigurationError,
        EnvFileError,
        DatabaseConfigError,
        StoreNotMigratedError,
    ) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    log = json.loads(run_log.read_text(encoding="utf-8")) if run_log.exists() else {}
    try:
        with Session(engine) as session:
            leads = load_active_leads(session)
    finally:
        engine.dispose()
    typer.echo(render(score(leads, generator.load(generator.ANSWER_KEY), log)))
