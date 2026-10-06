"""`leadforge` command-line entrypoint.

``ingest`` calls the composition root (``ingest_runner.run_ingestion``), prints the
per-source summary and the run report, and exits with the code the run maps to: zero
when at least one source succeeded, one when none did (Requirements 6.4 and 6.5), two
when the configuration is unusable, three when another run holds the store's run lock
(the run did not start and spent nothing). Configuration errors never carry a secret
or value.

Logging (follow-up 2026-10-06, 21.3): before the run, the command loads the ``.env``
and installs the redaction chain (``log_redaction.configure_logging``), so every log
line of a production run is scrubbed of credential values and raw payloads and goes
to stderr as JSON, apart from the report on stdout.
"""

import asyncio
import os

import typer

from leadforge.lead_ingestion.database import DatabaseConfigError
from leadforge.lead_ingestion.env_file import EnvFileError, load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError, run_ingestion
from leadforge.lead_ingestion.log_redaction import configure_logging

EXIT_CONFIGURATION_ERROR = 2
EXIT_RUN_IN_PROGRESS = 3

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """LeadForge pipeline commands."""


@app.command()
def ingest() -> None:
    """Run the Lead Ingestion Layer."""
    try:
        # The .env is loaded first (it never overrides the process, so the run's own
        # load is a no-op) so the redactor is seeded with every credential in reach.
        load_env_file_into_process()
        configure_logging(os.environ)
        outcome = asyncio.run(run_ingestion())
    except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    except RunInProgressError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(EXIT_RUN_IN_PROGRESS) from None
    typer.echo(outcome.exit.summary)
    typer.echo("")
    typer.echo(outcome.report_text)
    raise typer.Exit(outcome.exit.exit_code)
