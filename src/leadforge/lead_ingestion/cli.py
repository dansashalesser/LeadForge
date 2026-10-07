"""`leadforge` command-line entrypoint.

``ingest`` calls the composition root (``ingest_runner.run_ingestion``), prints the
per-source summary and the run report, and exits with the code the run maps to: zero
when at least one source succeeded, one when none did (Requirements 6.4 and 6.5), two
when the configuration is unusable, three when another run holds the store's run lock
(the run did not start and spent nothing). Configuration errors never carry a secret
or value.

Logging (follow-up 2026-10-06, 21.3): before the run, the command loads the ``.env``
and installs the redaction chain (``log_redaction.configure_logging``), so every log
line of a production run is scrubbed of credential values and raw payloads and goes
to stderr as JSON, apart from the report on stdout.

``leads show <lead_id>`` and ``leads list`` read stored leads back
(``store.lead_reader``; follow-up, user request 2026-10-06). Decision: the store is the
operator's own data, but a terminal is scrollback, screen shares and pasted
transcripts, so output masks the contact identifiers by default (an email as
``j***@acme.com``, a LinkedIn URL as ``host/***``) and ``--reveal`` prints them whole.
``--json`` prints the same, masked the same way. Stored text is provider text, so
the plain output escapes control characters (``run_report.printable``): a name
cannot send the terminal an escape sequence.
Names, companies and domains are printed: they are what an operator recognises a lead
by. The commands log nothing about a lead, with or without ``--reveal``. There is no
phone field in the model, so none is printed. Exit codes: one when the lead does not
exist, two on a configuration error (and for a malformed id, Typer's usage error).
A store whose schema ``ingest`` has not created or upgraded is a configuration error
too: the readers never migrate, they name the command that does.
"""

import asyncio
import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import typer
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.database import DatabaseConfigError, create_store_engine
from leadforge.lead_ingestion.demo.cli import demo_app
from leadforge.lead_ingestion.env_file import EnvFileError, load_env_file_into_process
from leadforge.lead_ingestion.errors import ConfigurationError
from leadforge.lead_ingestion.ingest_runner import RunInProgressError, run_ingestion
from leadforge.lead_ingestion.log_redaction import MASK, configure_logging
from leadforge.lead_ingestion.match_keys import normalize_linkedin_url
from leadforge.lead_ingestion.run_report import printable
from leadforge.lead_ingestion.store.lead_reader import (
    DEFAULT_PAGE,
    StoredLead,
    WebEvidence,
    list_leads,
    load_lead,
)
from leadforge.lead_ingestion.store.migrate import StoreNotMigratedError, require_head
from leadforge.lead_ingestion.web.cli import serve

EXIT_CONFIGURATION_ERROR = 2
EXIT_RUN_IN_PROGRESS = 3
EXIT_NOT_FOUND = 1

app = typer.Typer(no_args_is_help=True, add_completion=False)
leads_app = typer.Typer(no_args_is_help=True, help="Read stored leads.")
app.add_typer(leads_app, name="leads")
app.add_typer(demo_app, name="demo")
app.command("web")(serve)


@app.callback()
def main() -> None:
    """LeadForge pipeline commands."""


@app.command()
def ingest() -> None:
    """Run the Lead Ingestion Layer."""
    try:
        # The .env is loaded first (it never overrides the process, so the run's own
        # load is a no-op) so the redactor is seeded with every credential in reach.
        load_env_file_into_process()
        configure_logging(os.environ)
        outcome = asyncio.run(run_ingestion())
    except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    except RunInProgressError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(EXIT_RUN_IN_PROGRESS) from None
    typer.echo(outcome.exit.summary)
    typer.echo("")
    typer.echo(outcome.report_text)
    raise typer.Exit(outcome.exit.exit_code)


@contextmanager
def _session() -> Iterator[Session]:
    """A session on the configured store, after the same setup as ``ingest``; the
    engine is disposed on exit."""
    try:
        load_env_file_into_process()
        configure_logging(os.environ)
        engine = create_store_engine()
    except (ConfigurationError, EnvFileError, DatabaseConfigError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    try:
        require_head(engine)
    except StoreNotMigratedError as error:
        engine.dispose()
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_CONFIGURATION_ERROR) from None
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _email(address: object, reveal: bool) -> str | None:
    if address is None:
        return None
    text = str(address)
    if reveal:
        return text
    local, _, domain = text.rpartition("@")
    return f"{local[:1]}{MASK}@{domain}"


def _linkedin(url: object, reveal: bool) -> str | None:
    if url is None:
        return None
    if reveal:
        return str(url)
    host = (normalize_linkedin_url(str(url)) or "").partition("/")[0]
    return f"{host}/{MASK}"


def _company(stored: StoredLead) -> str:
    names = [e.company.name or e.company.company_id for e in stored.lead.employments]
    return ", ".join(names) or "-"


def _status(stored: StoredLead) -> str:
    if not stored.retired:
        return "active"
    return "retired -> " + (", ".join(map(str, stored.successor_ids)) or "none")


def _show_lines(stored: StoredLead, reveal: bool) -> Iterator[str]:
    lead = stored.lead
    yield f"lead {stored.lead_id} ({_status(stored)})"
    yield f"name: {lead.full_name or '-'}"
    role = ", role address" if lead.email_is_role_address else ""
    email = _email(lead.email, reveal) or "-"
    yield f"email: {email} ({lead.email_status.value}{role})"
    contacts = [_email(e, reveal) or "-" for e in lead.role_contact_emails]
    yield f"role contacts: {', '.join(contacts) or '-'}"
    yield f"linkedin: {_linkedin(lead.linkedin_url, reveal) or '-'}"
    for e in lead.employments:
        c = e.company
        current = {True: "current", False: "past", None: "unknown"}[e.is_current]
        yield (
            f"company: {c.name or '-'} [{c.company_id}] domains: "
            f"{', '.join(c.domains) or '-'}; title: {e.title or '-'} ({current})"
        )
    if stored.primary_domain_source is not None:
        flag = " (flagged tie)" if stored.primary_domain_flagged else ""
        yield (
            f"primary domain: {stored.primary_domain or '-'} "
            f"[{stored.primary_domain_source.value}]{flag}"
        )
    for kind, signals in (("tech", lead.tech_signals), ("intent", lead.intent_signals)):
        if signals:
            listed = ", ".join(f"{s.label} {s.strength:g}" for s in signals)
            yield f"{kind} signals: {listed}"
    yield f"opt-out: {lead.opt_out}; suppressed: {lead.suppressed}"
    stale = {None: "unknown", True: "yes", False: "no"}[stored.stale]
    yield (
        f"projection: version {stored.projection_version}, stale: {stale}, "
        f"computed {stored.computed_at.isoformat()}"
    )
    yield f"sources: {', '.join(stored.contributing_sources) or '-'}"
    agreeing = dict(stored.agreement)
    winners: set[str] = set()
    for p in stored.provenance:
        confidence = "none" if p.confidence is None else f"{p.confidence:g}"
        if p.superseded:
            state = "superseded"
        elif p.canonical_path in winners:
            state = "agrees"
        else:
            winners.add(p.canonical_path)
            state = f"winner, {agreeing.get(p.canonical_path, 1)} agree"
        yield (
            f"  {p.canonical_path}: {p.source_name} ({state}; confidence "
            f"{confidence}, {p.confidence_origin.value})"
        )
    for w in stored.web_evidence:
        yield f"web evidence: {w.source_name} {w.attachment.value} {_url(w)}"


def _url(evidence: WebEvidence) -> str:
    url = evidence.values.get("url")
    return "-" if url is None else str(url)


def _as_json(stored: StoredLead, reveal: bool) -> dict[str, Any]:
    """The lead for ``--json``, masked as the text output is."""
    lead = stored.lead.model_dump(mode="json")
    lead["email"] = _email(stored.lead.email, reveal)
    lead["linkedin_url"] = _linkedin(stored.lead.linkedin_url, reveal)
    lead["role_contact_emails"] = [
        _email(e, reveal) for e in stored.lead.role_contact_emails
    ]
    return {
        "lead_id": str(stored.lead_id),
        "retired_at": None if stored.retired_at is None else str(stored.retired_at),
        "successor_ids": [str(i) for i in stored.successor_ids],
        "lead": lead,
        "provenance": [p.model_dump(mode="json") for p in stored.provenance],
        "agreement": dict(stored.agreement),
        "contributing_sources": list(stored.contributing_sources),
        "primary_domain": stored.primary_domain,
        "primary_domain_source": (
            None
            if stored.primary_domain_source is None
            else stored.primary_domain_source.value
        ),
        "primary_domain_flagged": stored.primary_domain_flagged,
        "projection_version": stored.projection_version,
        "stale": stored.stale,
        "computed_at": stored.computed_at.isoformat(),
        "web_evidence": [
            {
                "source_name": w.source_name,
                "attachment": w.attachment.value,
                "domains": list(w.domains),
                "url": _url(w),
            }
            for w in stored.web_evidence
        ],
    }


def _echo(text: str) -> None:
    """Stored text is provider text: escaped, so it cannot drive the terminal."""
    typer.echo(printable(text))


_REVEAL = typer.Option("--reveal", help="Print emails and URLs whole.")
_JSON = typer.Option("--json", help="Print JSON (masked the same way).")


@leads_app.command("show")
def show(
    lead_id: uuid.UUID,
    reveal: Annotated[bool, _REVEAL] = False,
    as_json: Annotated[bool, _JSON] = False,
) -> None:
    """Print one stored lead, retired or not."""
    with _session() as session:
        stored = load_lead(session, lead_id)
    if stored is None:
        typer.echo(f"no lead {lead_id}", err=True)
        raise typer.Exit(EXIT_NOT_FOUND)
    if as_json:
        typer.echo(json.dumps(_as_json(stored, reveal), sort_keys=True))
        return
    for line in _show_lines(stored, reveal):
        _echo(line)


@leads_app.command("list")
def list_(
    limit: Annotated[int, typer.Option(min=1, help="Leads per page.")] = DEFAULT_PAGE,
    after: Annotated[
        uuid.UUID | None, typer.Option(help="Cursor: the last id shown.")
    ] = None,
    include_retired: Annotated[bool, typer.Option("--include-retired")] = False,
    company_id: Annotated[
        str | None, typer.Option(help="Only leads at this company.")
    ] = None,
    reveal: Annotated[bool, _REVEAL] = False,
    as_json: Annotated[bool, _JSON] = False,
) -> None:
    """Print stored leads, one line each, ordered by id."""
    with _session() as session:
        page = list_leads(
            session,
            include_retired=include_retired,
            company_id=company_id,
            limit=limit,
            after=after,
        )
    cursor = page[-1].lead_id if len(page) == limit else None
    if as_json:
        body = {
            "leads": [_as_json(stored, reveal) for stored in page],
            "next_after": None if cursor is None else str(cursor),
        }
        typer.echo(json.dumps(body, sort_keys=True))
        return
    for stored in page:
        _echo(
            f"{stored.lead_id}  {_status(stored)}  "
            f"{_email(stored.lead.email, reveal) or '-'}"
            f"  {stored.lead.full_name or '-'}  {_company(stored)}"
        )
    if cursor is not None:
        typer.echo(f"next: --after {cursor}")
