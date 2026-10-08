"""Background jobs the web UI starts: an ingestion run, a demo run, a demo generate.

One job at a time per server process. ``run_ingestion`` reads its store and mode from
the process environment (``DATABASE_URL``, ``LEADFORGE_MODE``), so a job sets those for
its own duration and puts them back after; the UI's reads never depend on them (they
name their store explicitly). The store's own run lock still stops a second run from
another process.

A job's text is what the CLI would print: the exit summary and the run report, which
carry no personal data. An error is reported by its message for the configuration
errors the CLI already prints, else by its class only.
"""

import asyncio
import json
import os
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import structlog

from leadforge.lead_ingestion.database import DatabaseConfigError
from leadforge.lead_ingestion.demo import generator
from leadforge.lead_ingestion.demo.cli import DEMO_RETRY, RUN_LOG, demo_profile
from leadforge.lead_ingestion.demo.transport import demo_transport_factory
from leadforge.lead_ingestion.env_file import EnvFileError
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import (
    GLOBAL_MODE_VARIABLE,
    IngestionOutcome,
    RunInProgressError,
    run_ingestion,
)

__all__ = ["Job", "JobBusyError", "JobKind", "JobRunner"]

_log = structlog.get_logger(__name__)

_DATABASE_URL = "DATABASE_URL"


class JobKind(StrEnum):
    INGEST = "ingest"
    DEMO_RUN = "demo_run"
    DEMO_GENERATE = "demo_generate"


class JobBusyError(RuntimeError):
    """A job is already running in this server."""


@dataclass
class Job:
    job_id: uuid.UUID
    kind: JobKind
    options: dict[str, bool]
    started_at: datetime
    finished_at: datetime | None = None
    state: str = "running"  # running | succeeded | failed
    exit_code: int | None = None
    summary: str = ""
    report: str = ""
    error: str | None = None
    run_id: uuid.UUID | None = None
    files: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, object]:
        return {
            "job_id": str(self.job_id),
            "kind": self.kind.value,
            "options": self.options,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at and self.finished_at.isoformat(),
            "state": self.state,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "report": self.report,
            "error": self.error,
            "run_id": self.run_id and str(self.run_id),
            "files": self.files,
        }


@contextmanager
def _environ(values: dict[str, str | None]) -> Iterator[None]:
    """Set (or, for None, unset) variables for the block; restore them after."""
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class JobRunner:
    """Starts jobs on a worker thread; keeps the current (or last) one."""

    def __init__(self, *, main_url: str | None, demo_url: str, demo_db: Path) -> None:
        # ``main_url`` None: the operator set no DATABASE_URL (the local default).
        self._main_url = main_url
        self._demo_url = demo_url
        self._demo_db = demo_db
        self._lock = threading.Lock()
        self._job: Job | None = None

    @property
    def current(self) -> Job | None:
        return self._job

    def start(self, kind: JobKind, *, fresh: bool = False, faults: bool = False) -> Job:
        with self._lock:
            if self._job is not None and self._job.state == "running":
                raise JobBusyError(f"a {self._job.kind.value} job is still running")
            options = {"fresh": fresh, "faults": faults}
            job = Job(uuid.uuid4(), kind, options, datetime.now(UTC))
            self._job = job
        work: Callable[[Job], None] = {
            JobKind.INGEST: self._ingest,
            JobKind.DEMO_RUN: self._demo_run,
            JobKind.DEMO_GENERATE: self._demo_generate,
        }[kind]
        threading.Thread(
            target=self._guarded,
            args=(work, job),
            name=f"leadforge-{kind}",
            daemon=True,
        ).start()
        return job

    def _guarded(self, work: Callable[[Job], None], job: Job) -> None:
        try:
            work(job)
        except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
            job.error = f"configuration error: {error}"
        except RunInProgressError as error:
            job.error = str(error)
        except Exception as error:  # noqa: BLE001 - reported to the UI by class only
            _log.error(
                "web_job_failed", kind=job.kind.value, error=type(error).__name__
            )
            job.error = f"{type(error).__name__} (see the server log)"
        job.finished_at = datetime.now(UTC)
        job.state = (
            "succeeded"
            if job.error is None and job.exit_code in (0, None)
            else "failed"
        )

    @staticmethod
    def _record(job: Job, outcome: IngestionOutcome) -> None:
        job.run_id = outcome.run_id
        job.exit_code = outcome.exit.exit_code
        job.summary = outcome.exit.summary
        job.report = outcome.report_text

    def _ingest(self, job: Job) -> None:
        with _environ({_DATABASE_URL: self._main_url}):
            self._record(job, asyncio.run(run_ingestion()))

    def _demo_run(self, job: Job) -> None:
        if job.options["fresh"]:
            self._demo_db.unlink(missing_ok=True)
        self._demo_db.parent.mkdir(parents=True, exist_ok=True)
        factory, log = demo_transport_factory(faults=job.options["faults"])
        env: dict[str, str | None] = {
            _DATABASE_URL: self._demo_url,
            GLOBAL_MODE_VARIABLE: "synthetic",
        }
        with _environ(env):
            outcome = asyncio.run(
                run_ingestion(
                    target_profile=demo_profile(),
                    transport_factory=factory,
                    synthetic_retry=DEMO_RETRY,
                )
            )
        run_log = self._demo_db.parent / RUN_LOG
        run_log.write_text(json.dumps(log.as_json(), indent=1) + "\n", encoding="utf-8")
        self._record(job, outcome)

    def _demo_generate(self, job: Job) -> None:
        job.files = [str(p) for p in generator.write()]
        job.summary = f"wrote {len(job.files)} files"
