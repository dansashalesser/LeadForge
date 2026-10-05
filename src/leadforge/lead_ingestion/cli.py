"""`leadforge` command-line entrypoint.

``ingest`` calls the composition root (``ingest_runner.run_ingestion``), prints the
per-source summary and the run report, and exits with the code the run maps to: zero
when at least one source succeeded, one when none did (Requirements 6.4 and 6.5), two
when the configuration is unusable. Configuration errors never carry a secret or value.
"""

import asyncio

import typer

from leadforge.lead_ingestion.database import DatabaseConfigError
from leadforge.lead_ingestion.env_file import EnvFileError
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import run_ingestion

EXIT_CONFIGURATION_ERROR = 2

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """LeadForge pipeline commands."""


@app.command()
def ingest() -> None:
    """Run the Lead Ingestion Layer."""
    try:
        outcome = asyncio.run(run_ingestion())
    except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    typer.echo(outcome.exit.summary)
    typer.echo("")
    typer.echo(outcome.report_text)
    raise typer.Exit(outcome.exit.exit_code)
