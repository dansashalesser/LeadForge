"""The ``leadforge`` command: ingestion commands, outreach commands and the web app.

The root composes the slices so neither imports the other: ingestion never knows
outreach exists. ``leadforge ingest|leads|demo`` are ingestion's; ``leadforge outreach
search|tick|messages|report`` are outreach's; ``leadforge web`` serves the ingestion UI
with the outreach API and page (``/outreach``) added to it.
"""

from leadforge.lead_ingestion.cli import app
from leadforge.lead_ingestion.web.cli import serve_command
from leadforge.outreach.cli import outreach_app
from leadforge.outreach.web import create_router

__all__ = ["app"]

app.add_typer(outreach_app, name="outreach")
# Replace ingestion's plain ``web`` with one that also serves the outreach router.
app.registered_commands = [c for c in app.registered_commands if c.name != "web"]
app.command("web")(serve_command([create_router()]))
