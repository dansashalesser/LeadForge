"""Outreach: turns ingested Leads into a reviewed, dry-run outreach sequence report.

A vertical slice of its own. It reads Leads only through the ingestion slice's public
API (see ``tests/test_boundary_guards.py`` for the exact allowlist), and ingestion
never imports it. Nothing here sends: no network transport or mail library is
imported, and the dispatcher writes to the database, the console and a file.
"""
