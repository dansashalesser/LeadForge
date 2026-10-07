"""``leadforge web``: serve the UI on this machine.

``.env`` is loaded and logging configured first, as ``ingest`` does, so a run the UI
starts sees the same keys and the same redaction. The server binds to localhost: it
has no login, and it shows leads and spends provider credits on request.
"""

import os
import webbrowser
from typing import Annotated

import typer
import uvicorn

from leadforge.lead_ingestion.demo.cli import DEFAULT_DB
from leadforge.lead_ingestion.env_file import EnvFileError, load_env_file_into_process
from leadforge.lead_ingestion.log_redaction import configure_logging
from leadforge.lead_ingestion.web.api import create_app

EXIT_CONFIGURATION_ERROR = 2


def serve(
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1.")] = 8710,
    open_browser: Annotated[
        bool, typer.Option("--open/--no-open", help="Open the UI in a browser.")
    ] = True,
) -> None:
    """Serve the web UI on http://127.0.0.1:<port>."""
    try:
        load_env_file_into_process()
    except EnvFileError as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    configure_logging(os.environ)
    app = create_app(
        main_url=os.environ.get("DATABASE_URL", "").strip() or None, demo_db=DEFAULT_DB
    )
    url = f"http://127.0.0.1:{port}"
    typer.echo(f"LeadForge UI on {url} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
