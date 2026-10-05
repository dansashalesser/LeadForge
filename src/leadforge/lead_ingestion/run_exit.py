"""Map a run's outcomes to a process exit code and summary (task 11.3).

Requirements 6.4 and 6.5. A pure function over the ``SourceResult`` records of
``IngestionOrchestrator.run``; it names no adapter and does no I/O.

Provisional decisions (see choices.md, task 11.3):

* A source succeeded when at least one of its calls succeeded; otherwise it failed
  (this covers a halted source, whose skipped calls are never successes).
* Exit 0 requires at least one succeeded source (6.5). Everything else exits 1: every
  enabled source failed (6.4), or none was enabled (nothing succeeded).
* Disabled sources are absent from the results, so they never count as failures.
* The summary holds source names, failure classes and counts only; ``outcome.error``
  is never copied into it.
"""

from __future__ import annotations

from dataclasses import dataclass

from leadforge.lead_ingestion.orchestrator import SourceResult, SourceStatus

__all__ = ["EXIT_ALL_FAILED", "EXIT_OK", "RunExit", "map_run_exit"]

EXIT_OK = 0
EXIT_ALL_FAILED = 1


@dataclass(frozen=True)
class RunExit:
    exit_code: int
    summary: str


def _printable(text: str) -> str:
    """Escape control characters so a source name cannot add or overwrite lines."""
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode() for c in text
    )


def map_run_exit(results: tuple[SourceResult, ...]) -> RunExit:
    if not results:
        return RunExit(EXIT_ALL_FAILED, "no enabled sources; nothing succeeded")
    lines = []
    any_succeeded = False
    for result in results:
        outcome = result.outcome
        name = _printable(outcome.source_name)
        counts = (
            f"attempted={outcome.attempted} "
            f"succeeded={outcome.succeeded} failed={outcome.failed}"
        )
        if outcome.succeeded > 0:
            any_succeeded = True
            lines.append(f"{name}: {counts}")
        else:
            # OK with no successful call is not a failure class.
            failure = (
                outcome.status.value
                if outcome.status is not SourceStatus.OK
                else "no_successful_calls"
            )
            lines.append(f"{name}: {failure} {counts}")
    if any_succeeded:
        return RunExit(EXIT_OK, "\n".join(lines))
    header = "all enabled sources failed"
    return RunExit(EXIT_ALL_FAILED, "\n".join([header, *lines]))
