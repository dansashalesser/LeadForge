"""Outreach commands: search per mode, advance time, list Messages, report.


``outreach search free-text|workers|users``, ``outreach tick``, ``outreach messages``
and ``outreach report`` (requirements 12.1, 12.2). A search prints its plan before it
spends, then a funnel summary and the outbox path. Like the ingestion commands, the
readers never migrate the store and exit 2 on a configuration error; output masks
contact identifiers unless ``--reveal`` is given, and stored text is escaped so a
Message cannot drive the terminal. This module only parses and prints: the work is in
``SearchService``, ``tick`` and ``build_report``.
"""

import asyncio
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.catalog import CatalogError, UnknownCatalogKeyError
from leadforge.lead_ingestion.database import DatabaseConfigError, create_store_engine
from leadforge.lead_ingestion.env_file import EnvFileError, load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError
from leadforge.lead_ingestion.log_redaction import configure_logging
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.outreach.acceptance import SeededAcceptance
from leadforge.outreach.clock import SystemClock
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.dispatch import DryRunDispatcher
from leadforge.outreach.errors import (
    MessageGenerationError,
    MissingDomainError,
    NoProductSelectedError,
    OutreachConfigError,
    PlanCompileError,
    UnknownModeError,
    UnknownTermError,
    UsageClassifierUnavailableError,
)
from leadforge.outreach.messages import stored_messages
from leadforge.outreach.report import build_report, render_json, render_markdown
from leadforge.outreach.runtime import build_service
from leadforge.outreach.scorecard import render as render_scorecard
from leadforge.outreach.scorecard import score_decisions
from leadforge.outreach.search_plan import Mode, SearchPlan, parse_request
from leadforge.outreach.service import SearchSummary
from leadforge.outreach.tables import OutreachDecision, OutreachSearch
from leadforge.outreach.tick import tick
from leadforge.outreach.usage.drafts import approve_draft
from leadforge.outreach.usage.eval import (
    DEFAULT_CASES,
    EvalCaseError,
    load_cases,
    render_report,
    run_eval,
)

__all__ = ["outreach_app"]

# The demo answer key ships beside the demo tables; it is data, so it is read as a file.
DEMO_ANSWER_KEY = (
    Path(__file__).resolve().parents[1]
    / "lead_ingestion"
    / "demo"
    / "data"
    / "answer_key.json"
)
EXIT_FAILED = 1
EXIT_CONFIGURATION_ERROR = 2
EXIT_RUN_IN_PROGRESS = 3

outreach_app = typer.Typer(no_args_is_help=True, help="Search, message and report.")
search_app = typer.Typer(no_args_is_help=True, help="Run a search in one mode.")
outreach_app.add_typer(search_app, name="search")
catalog_app = typer.Typer(no_args_is_help=True, help="Catalog drafts.")
outreach_app.add_typer(catalog_app, name="catalog")

_ESCAPE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_REVEAL = typer.Option("--reveal", help="Print emails, URLs and names whole.")
_SEARCH = typer.Option("--search", help="The search id.")


def _safe(text: str) -> str:
    return _ESCAPE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def _echo(text: str) -> None:
    typer.echo(_safe(text))


@contextmanager
def _failing_cleanly() -> Iterator[None]:
    try:
        yield
    except (
        ConfigurationError,
        OutreachConfigError,
        EnvFileError,
        DatabaseConfigError,
        StoreNotMigratedError,
    ) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    except RunInProgressError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(EXIT_RUN_IN_PROGRESS) from None
    except (
        UnknownModeError,
        UnknownTermError,
        MissingDomainError,
        NoProductSelectedError,
        PlanCompileError,
        MessageGenerationError,
        UnknownCatalogKeyError,
        CatalogError,
    ) as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(EXIT_FAILED) from None


def _setup() -> None:
    load_env_file_into_process()
    configure_logging(os.environ)


@contextmanager
def _session() -> Iterator[Session]:
    _setup()
    engine = create_store_engine()
    try:
        require_head(engine)
        with Session(engine) as session, session.begin():
            yield session
    finally:
        engine.dispose()


def _show_plan(plan: SearchPlan) -> None:
    typer.echo(f"plan ({plan.compiler} compiler), nothing is spent until it is shown:")
    typer.echo(_safe(plan.model_dump_json(indent=2)))


def _print_summary(summary: SearchSummary, outbox: Path) -> None:
    typer.echo("")
    typer.echo(f"search {summary.search_id}: {summary.gathered} gathered")
    for name, count in summary.counts.items():
        typer.echo(f"  {name}: {count}")
    typer.echo(f"  invited: {summary.invited}")
    for note in summary.notes:
        typer.echo(f"  {note}")
    typer.echo(f"outbox: {outbox}")


def _search(
    mode: Mode,
    query: str,
    domains: list[str],
    *,
    vendor: str | None = None,
    products: list[str] | None = None,
    include_ecosystem: bool | None = None,
    usage_budget: int | None = None,
) -> None:
    with _failing_cleanly():
        _setup()
        service = build_service(
            os.environ, include_ecosystem=include_ecosystem, usage_budget=usage_budget
        )
        plan = service.plan(
            parse_request(mode, query, domains, vendor=vendor, products=products or [])
        )
        summary = asyncio.run(service.run(plan, show=_show_plan))
        _print_summary(summary, service.outbox_path)


@search_app.command("free-text")
def free_text(query: str) -> None:
    """Search by describing who you want in plain words."""
    _search("free_text", query, [])


@search_app.command("workers")
def workers(
    company: str,
    domain: Annotated[
        list[str] | None, typer.Option("--domain", help="A domain of the company.")
    ] = None,
) -> None:
    """Search the people who work at a company."""
    _search("workers", company, domain or [])


@search_app.command("users")
def users(
    product: Annotated[
        list[str] | None,
        typer.Option("--product", help="A catalog product key (repeatable)."),
    ] = None,
    vendor: Annotated[
        str | None,
        typer.Option("--vendor", help="Catalog vendor key; its product names it."),
    ] = None,
    include_ecosystem: Annotated[
        bool,
        typer.Option(
            "--include-ecosystem",
            help="Also search the vendor's ecosystem technologies.",
        ),
    ] = False,
    usage_budget: Annotated[
        int | None,
        typer.Option("--usage-budget", min=0, help="Usage-search budget for this run."),
    ] = None,
) -> None:
    """Search the people and companies that use a vendor's catalog products."""
    _search(
        "users",
        vendor or ", ".join(product or ()) or "-",
        [],
        vendor=vendor,
        products=product,
        include_ecosystem=True if include_ecosystem else None,
        usage_budget=usage_budget,
    )


@catalog_app.command("approve")
def catalog_approve(
    key: Annotated[str, typer.Argument(help="The drafted vendor's key.")],
) -> None:
    """Approve a drafted vendor so searches may use it."""
    _setup()
    with _failing_cleanly():
        path = approve_draft(key)
    typer.echo(f"approved {key}: {path}")


@outreach_app.command("tick")
def tick_command(
    search: Annotated[uuid.UUID | None, _SEARCH] = None,
    days: Annotated[float, typer.Option("--days", help="Advance from now.")] = 0,
    at: Annotated[
        datetime | None, typer.Option("--now", help="Tick at this time (UTC).")
    ] = None,
) -> None:
    """Advance every sequence of a search (or of all searches) to a time."""
    if at is not None and days:
        raise typer.BadParameter("give --now or --days, not both")
    with _failing_cleanly():
        config = load_outreach_config()
        start = at.replace(tzinfo=at.tzinfo or UTC) if at else SystemClock().now()
        when = start + timedelta(days=days)
        dispatcher = DryRunDispatcher(
            config.outbox_path, lambda t: typer.echo(_safe(t))
        )
        with _session() as session:
            ids = (
                [search]
                if search is not None
                else list(session.scalars(select(OutreachSearch.id)))
            )
            fired = 0
            for search_id in ids:
                fired += len(
                    tick(
                        session,
                        search_id,
                        when,
                        cfg=config.triggers,
                        acceptance=SeededAcceptance(config.simulation),
                        dispatcher=dispatcher,
                    )
                )
        typer.echo(f"tick at {when.isoformat()}: {fired} action(s)")
        typer.echo(f"outbox: {config.outbox_path}")


@outreach_app.command("messages")
def messages(search: Annotated[uuid.UUID, _SEARCH]) -> None:
    """List the Messages written for a search."""
    with _failing_cleanly(), _session() as session:
        decisions = session.scalars(
            select(OutreachDecision)
            .where(OutreachDecision.search_id == search)
            .order_by(OutreachDecision.lead_id)
        )
        shown = 0
        for decision in decisions:
            for message in stored_messages(session, decision.id):
                shown += 1
                head = f"{decision.id} {message.channel} [{message.state}] "
                _echo(
                    head
                    + f"{message.generator}/{message.model}/{message.prompt_version}"
                )
                if message.subject:
                    _echo(f"  subject: {message.subject}")
                for line in message.body.splitlines():
                    _echo(f"  {line}")
        typer.echo(f"{shown} message(s)")


@outreach_app.command("report")
def report(
    search: Annotated[uuid.UUID, _SEARCH],
    fmt: Annotated[str, typer.Option("--format", help="md or json.")] = "md",
    reveal: Annotated[bool, _REVEAL] = False,
) -> None:
    """Print the report of a search."""
    if fmt not in ("md", "json"):
        raise typer.BadParameter("format must be md or json")
    with _failing_cleanly(), _session() as session:
        if session.get(OutreachSearch, search) is None:
            typer.echo(f"no such search: {search}", err=True)
            raise typer.Exit(EXIT_FAILED)
        built = build_report(session, search, reveal=reveal)
        text = render_json(built) if fmt == "json" else render_markdown(built)
        _echo(text)


@outreach_app.command("scorecard")
def scorecard(
    search: Annotated[uuid.UUID, _SEARCH],
    answer_key: Annotated[
        Path | None,
        typer.Option(
            "--answer-key", help="The demo answer key (default: the bundled)."
        ),
    ] = None,
) -> None:
    """Compare a demo search's Decisions with the answer key."""
    key_path = answer_key or DEMO_ANSWER_KEY
    if not key_path.is_file():
        raise typer.BadParameter(f"no answer key at {key_path}")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    with _failing_cleanly(), _session() as session:
        if session.get(OutreachSearch, search) is None:
            typer.echo(f"no such search: {search}", err=True)
            raise typer.Exit(EXIT_FAILED)
        typer.echo(render_scorecard(score_decisions(session, search, key)))


@outreach_app.command("usage-eval")
def usage_eval(
    cases: Annotated[
        Path, typer.Option("--cases", help="Labelled cases (default: the seed set).")
    ] = DEFAULT_CASES,
    classifier: Annotated[
        str, typer.Option("--classifier", help="offline (default, no key) or llm.")
    ] = "offline",
) -> None:
    """Score the usage classifier on labelled cases. Not part of CI (live runs)."""
    from leadforge.outreach.usage.classify import OfflineClassifier
    from leadforge.outreach.usage.cues import UsageCues

    if classifier not in ("offline", "llm"):
        raise typer.BadParameter("classifier must be offline or llm")
    if classifier == "llm":
        # The live classifier factory (task 6.3) is not built yet; never fall back.
        typer.echo(
            f"{UsageClassifierUnavailableError()}: the live classifier factory "
            "(task 6.3) is pending",
            err=True,
        )
        raise typer.Exit(EXIT_CONFIGURATION_ERROR)
    try:
        loaded = load_cases(cases)
    except EvalCaseError as exc:
        typer.echo(_safe(str(exc)), err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from exc
    _echo(render_report(run_eval(loaded, OfflineClassifier(UsageCues()))))
