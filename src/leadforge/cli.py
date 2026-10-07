"""The ``leadforge`` command: ingestion commands and outreach commands in one app.

The root composes the two slices so neither imports the other: ingestion never knows
outreach exists. ``leadforge ingest|leads|demo|web`` are ingestion's; ``leadforge
outreach search|tick|messages|report`` are outreach's.
"""

from leadforge.lead_ingestion.cli import app
from leadforge.outreach.cli import outreach_app

__all__ = ["app"]

app.add_typer(outreach_app, name="outreach")
