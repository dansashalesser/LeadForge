"""`leadforge` command-line entrypoint.

Task 1.1 ships `ingest` as a stub: it resolves the ingestion slice and reports a
placeholder outcome. Real orchestration and exit-code mapping arrive in later tasks.
"""

import importlib

import typer

SLICE_MODULE = "leadforge.lead_ingestion"
PLACEHOLDER_MESSAGE = "ingestion not implemented yet (placeholder outcome)"

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """LeadForge pipeline commands."""


@app.command()
def ingest() -> None:
    """Run the Lead Ingestion Layer."""
    slice_pkg = importlib.import_module(SLICE_MODULE)
    typer.echo(f"{slice_pkg.__name__}: {PLACEHOLDER_MESSAGE}")
