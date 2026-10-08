"""A neutral Target Profile for ingestion-run tests, built from ``catalog_fixture/``.

Replaces the old profile file: the profile is built through the catalog, as production
does, from a vendor-free catalog directory. Lives under ``fixtures``, where source
names are allowed, so the tests that use it need not name a source.
"""

from pathlib import Path

import pytest

from leadforge.lead_ingestion import cli
from leadforge.lead_ingestion.catalog import load_catalog
from leadforge.lead_ingestion.target_profile import TargetProfile

CATALOG_FIXTURE = Path(__file__).resolve().parent.parent / "catalog_fixture"
UID_SOURCE = "apollo"
ALIAS_SOURCE = "google_search"
KEYWORD_TEMPLATES = ("{term} migration", "hiring {term} engineer")


def fixture_profile() -> TargetProfile:
    """Acme is the target, Rival its competitor; one UID source, one phrase source."""
    built = load_catalog(CATALOG_FIXTURE).to_profile(
        "acme",
        competitors=("rival",),
        uid_source=UID_SOURCE,
        alias_source=ALIAS_SOURCE,
    )
    return TargetProfile(
        technologies=built.technologies,
        competitors=built.competitors,
        keyword_templates=KEYWORD_TEMPLATES,
    )


def ingest_args(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """CLI arguments for ``ingest`` that select the fixture catalog's target vendor."""
    monkeypatch.setattr(cli, "load_catalog", lambda: load_catalog(CATALOG_FIXTURE))
    return [
        "ingest",
        "--vendor",
        "acme",
        "--uid-source",
        UID_SOURCE,
        "--alias-source",
        ALIAS_SOURCE,
    ]
