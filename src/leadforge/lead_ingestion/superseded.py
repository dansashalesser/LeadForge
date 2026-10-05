"""Losing values retained as superseded provenance (task 16.4; Requirements 8.5-8.7).

Pure functions over a ``ClusterResolution``. Nothing is stored, edited or deleted: the
contribution log stays append-only and the marking is derived, so every call builds
new frozen ``FieldProvenance`` objects. Provisional decisions (choices.md, 16.4):

* Only ``superseded`` candidates (a different value from the winner) are marked.
  The winner and the ``agreeing`` candidates stay unmarked: they say the same thing,
  so they are corroboration (8.7 counts them), not a contradiction to be flagged.
* ``retain_superseded`` returns a ``ClusterResolution`` again, so it is idempotent
  and 16.5 consumes the same type; ``provenance_records`` flattens it into the rows
  to persist: by path, then winner, agreeing, superseded, each in total order.
* The mark is derived, never trusted from the input: a winner or agreeing candidate
  carrying a stale ``superseded`` flag (from an earlier run) is cleared, so the result
  depends only on the resolution's structure (8.12) and is idempotent.
* ``agreeing_source_count`` counts distinct sources holding the winning value, the
  winner's own included. ``contributing_sources`` is the sorted distinct names seen in
  any candidate or absence; a contribution with neither is not visible from here.
* Absences are not field values: they pass through untouched and never become
  provenance records.
"""

from dataclasses import replace

from leadforge.lead_ingestion.conflicts import (
    ClusterResolution,
    FieldCandidate,
    FieldResolution,
)
from leadforge.lead_ingestion.models import FieldProvenance

__all__ = [
    "agreeing_source_count",
    "contributing_sources",
    "provenance_records",
    "retain_superseded",
]


def retain_superseded(resolution: ClusterResolution) -> ClusterResolution:
    """Return ``resolution`` with each losing candidate marked superseded."""
    return replace(resolution, fields=tuple(_mark_field(f) for f in resolution.fields))


def provenance_records(resolution: ClusterResolution) -> tuple[FieldProvenance, ...]:
    """Every candidate's provenance, losers marked, ordered for persistence."""
    return tuple(
        c.provenance
        for f in retain_superseded(resolution).fields
        for c in (f.winner, *f.agreeing, *f.superseded)
    )


def agreeing_source_count(resolution: FieldResolution) -> int:
    """Distinct sources holding the winning value, the winner's own included (8.7)."""
    return len({c.source_name for c in (resolution.winner, *resolution.agreeing)})


def contributing_sources(resolution: ClusterResolution) -> tuple[str, ...]:
    """Sorted distinct names of every source that contributed a value or an absence."""
    names = {
        c.source_name
        for f in resolution.fields
        for c in (f.winner, *f.agreeing, *f.superseded)
    }
    names.update(a.source_name for a in resolution.negative_evidence)
    names.update(a.source_name for a in resolution.not_applicable)
    return tuple(sorted(names))


def _mark_field(field: FieldResolution) -> FieldResolution:
    return replace(
        field,
        winner=_set_mark(field.winner, False),
        agreeing=tuple(_set_mark(c, False) for c in field.agreeing),
        superseded=tuple(_set_mark(c, True) for c in field.superseded),
    )


def _set_mark(candidate: FieldCandidate, superseded: bool) -> FieldCandidate:
    if candidate.provenance.superseded is superseded:
        return candidate
    return replace(
        candidate,
        provenance=candidate.provenance.model_copy(update={"superseded": superseded}),
    )
