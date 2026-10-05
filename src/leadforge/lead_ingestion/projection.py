"""The Lead projection, recomputed non-destructively (task 16.5; Requirement 8.12).

``project_lead`` is a pure function from one ``IdentityCluster`` and a Source Trust
Rank mapping to a ``ProjectionResult``: the ``CanonicalLead`` (when the cluster names
a person), its provenance records with the losers marked superseded, the
contributing-source set and the per-path agreeing-source counts (8.5-8.7). It reads
the stored contributions and nothing else, never edits or deletes one, and builds
everything new, so it can be re-run at any time (a changed trust rank, a new run) and
the previous result simply replaced; there is no unmerge or split anywhere (8.12).
Contribution order does not matter (8.8): conflicts are resolved under 16.3's total
order and every collection built here is sorted. Persisting the result and the
"one transaction" recompute belong to the Lead Store wiring and are not built here.
Provisional decisions (choices.md, 16.5):

* ``CanonicalLead`` has no field for the contributing sources or the provenance, so
  they ride on ``ProjectionResult`` beside it; the model is unchanged.
* A CRM source's bare ``email`` path is read as ``person.email``: the contribution is
  re-keyed in memory before resolving (the stored one is untouched), unless it already
  has a ``person.email`` of its own, in which case the bare path stays a separate,
  unmapped path.
* Scalar fields take the 16.3 winner for their path. A winner that fails the model's
  own validation (an unparseable address or URL) leaves the field empty; no lower
  candidate is promoted in its place, so the field still resolves to the winning
  provenance record.
* ``email_status`` is only meaningful for the address it was stated about, so it is
  taken from the first candidate (winner, agreeing, superseded) whose source also
  supplied the chosen address; otherwise ``unknown``. A status with no email is never
  built, so the model's validator cannot reject the merge.
* ``opt_out`` and ``suppressed`` are the OR over every candidate of every source, not
  the winner: a flag from any source is never lost to a conflict (design: OR
  semantics). They are also kept on the result, so a cluster that names no person
  (a provider restriction that carries only a name or a domain) still reports them.
* A cluster with no email, LinkedIn URL or name forms no Lead: ``lead`` is ``None``
  and everything else is still returned. Not an error.
* ``full_name`` is ``person.full_name``, else ``person.first_name`` and
  ``person.last_name`` joined by a space (both required), as ``match_keys`` does.
* One ``Employment`` at most, from the winning ``company.name`` / ``company.domain``
  and ``person.title``. ``company_id`` is a hash of the sorted, casefolded domain set
  (of the casefolded name when there is no domain); company clustering (16.9-16.11)
  will replace it. ``is_current`` stays ``None``: no canonical path states it. With no
  organization name or domain there is no Employment and the title stays in
  provenance only.
* Signals are evidence, not competing values: every ``TechSignal`` and
  ``IntentSignal`` found in any candidate of any path is carried, one per (kind,
  label), the first in candidate order (path, winner, agreeing, superseded) keeping
  its strength, so Signal Strength never decides anything (24.4). Flat adapter values
  that are not ``Signal`` objects (the flat ``company.technologies`` list) are not
  turned into Signals here.
* Personal data stays out of ``repr`` and no error text is built from it.
"""

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from pydantic import HttpUrl, TypeAdapter, ValidationError

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.conflicts import (
    ClusterResolution,
    FieldCandidate,
    resolve_conflicts,
)
from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    EmailStatus,
    Employment,
    FieldProvenance,
    IntentSignal,
    SourceAbsence,
    StrictEmail,
    TechSignal,
    UntrustedText,
)
from leadforge.lead_ingestion.superseded import (
    agreeing_source_count,
    contributing_sources,
    provenance_records,
)

__all__ = ["ProjectionResult", "project_lead"]

_BARE_EMAIL = "email"
_EMAIL = "person.email"
_EMAIL_STATUS = "person.email_status"
_LINKEDIN = "person.linkedin_url"
_FULL_NAME = "person.full_name"
_FIRST_NAME = "person.first_name"
_LAST_NAME = "person.last_name"
_TITLE = "person.title"
_COMPANY_NAME = "company.name"
_COMPANY_DOMAIN = "company.domain"
_OPT_OUT = "opt_out"
_SUPPRESSED = "suppressed"

_EMAIL_ADAPTER: TypeAdapter[str] = TypeAdapter(StrictEmail)
_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


@dataclass(frozen=True)
class ProjectionResult:
    """One cluster's projection; ``lead`` is ``None`` when it names no person."""

    lead: CanonicalLead | None = field(repr=False)
    contributing_sources: tuple[str, ...]
    # (canonical path, distinct sources holding the winning value), sorted by path.
    agreement: tuple[tuple[str, int], ...]
    provenance: tuple[FieldProvenance, ...] = field(repr=False)
    negative_evidence: tuple[SourceAbsence, ...] = field(repr=False)
    not_applicable: tuple[SourceAbsence, ...] = field(repr=False)
    opt_out: bool
    suppressed: bool


def project_lead(
    cluster: IdentityCluster, trust_ranks: Mapping[str, int]
) -> ProjectionResult:
    """Project ``cluster``; the result ignores contribution order."""
    members = tuple(_with_canonical_email(c) for c in cluster.contributions)
    resolution = resolve_conflicts(
        IdentityCluster(cluster.cluster_id, members), trust_ranks
    )
    sources = sorted(
        {c.source_name for c in members} | set(contributing_sources(resolution))
    )
    opt_out = _any_true(resolution, _OPT_OUT)
    suppressed = _any_true(resolution, _SUPPRESSED)
    return ProjectionResult(
        lead=_build_lead(resolution, opt_out, suppressed),
        contributing_sources=tuple(sources),
        agreement=tuple(
            (f.canonical_path, agreeing_source_count(f)) for f in resolution.fields
        ),
        provenance=provenance_records(resolution),
        negative_evidence=resolution.negative_evidence,
        not_applicable=resolution.not_applicable,
        opt_out=opt_out,
        suppressed=suppressed,
    )


def _with_canonical_email(contribution: LeadContribution) -> LeadContribution:
    """A copy keyed ``person.email`` instead of the bare ``email`` (a CRM path)."""
    if _BARE_EMAIL not in contribution.values or _EMAIL in contribution.values:
        return contribution
    values = {
        (_EMAIL if path == _BARE_EMAIL else path): value
        for path, value in contribution.values.items()
    }
    provenance = tuple(
        p.model_copy(update={"canonical_path": _EMAIL})
        if p.canonical_path == _BARE_EMAIL
        else p
        for p in contribution.provenance
    )
    absences = tuple(
        a.model_copy(update={"canonical_path": _EMAIL})
        if a.canonical_path == _BARE_EMAIL
        else a
        for a in contribution.absences
    )
    return contribution.model_copy(
        update={"values": values, "provenance": provenance, "absences": absences}
    )


def _candidates(resolution: ClusterResolution, path: str) -> tuple[FieldCandidate, ...]:
    for f in resolution.fields:
        if f.canonical_path == path:
            return (f.winner, *f.agreeing, *f.superseded)
    return ()


def _winner_value(resolution: ClusterResolution, path: str) -> object:
    candidates = _candidates(resolution, path)
    return candidates[0].value if candidates else None


def _any_true(resolution: ClusterResolution, path: str) -> bool:
    return any(c.value is True for c in _candidates(resolution, path))


def _text(value: object) -> str | None:
    if isinstance(value, UntrustedText):
        value = value.value
    if isinstance(value, str) and value.strip():
        return value
    return None


def _build_lead(
    resolution: ClusterResolution, opt_out: bool, suppressed: bool
) -> CanonicalLead | None:
    email = _valid_email(_text(_winner_value(resolution, _EMAIL)))
    linkedin = _valid_url(_text(_winner_value(resolution, _LINKEDIN)))
    full_name = _full_name(resolution)
    if email is None and linkedin is None and full_name is None:
        return None
    tech, intent = _signals(resolution)
    return CanonicalLead(
        email=email,
        email_status=_email_status(resolution, email),
        linkedin_url=linkedin,
        full_name=full_name,
        employments=_employments(resolution),
        tech_signals=tech,
        intent_signals=intent,
        opt_out=opt_out,
        suppressed=suppressed,
    )


def _valid_email(text: str | None) -> str | None:
    if text is None:
        return None
    try:
        return _EMAIL_ADAPTER.validate_python(text)
    except ValidationError:
        return None


def _valid_url(text: str | None) -> HttpUrl | None:
    if text is None:
        return None
    try:
        return _URL_ADAPTER.validate_python(text)
    except ValidationError:
        return None


def _full_name(resolution: ClusterResolution) -> str | None:
    name = _text(_winner_value(resolution, _FULL_NAME))
    if name is not None:
        return name
    first = _text(_winner_value(resolution, _FIRST_NAME))
    last = _text(_winner_value(resolution, _LAST_NAME))
    if first is None or last is None:
        return None
    return f"{first} {last}"


def _email_status(resolution: ClusterResolution, email: str | None) -> EmailStatus:
    if email is None:
        return EmailStatus.UNKNOWN
    wanted = email.strip().casefold()
    stated_by = {
        c.source_name
        for c in _candidates(resolution, _EMAIL)
        if (text := _text(c.value)) is not None and text.strip().casefold() == wanted
    }
    for candidate in _candidates(resolution, _EMAIL_STATUS):
        if candidate.source_name not in stated_by:
            continue
        try:
            return EmailStatus(candidate.value)
        except ValueError:
            return EmailStatus.UNKNOWN
    return EmailStatus.UNKNOWN


def _domains(value: object) -> tuple[str, ...]:
    items: Iterable[object]
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Collection):
        items = value
    else:
        return ()
    cleaned = {i.strip().casefold() for i in items if isinstance(i, str)}
    return tuple(sorted(d for d in cleaned if d and not any(c.isspace() for c in d)))


def _employments(resolution: ClusterResolution) -> tuple[Employment, ...]:
    name = _text(_winner_value(resolution, _COMPANY_NAME))
    domains = _domains(_winner_value(resolution, _COMPANY_DOMAIN))
    if name is None and not domains:
        return ()
    basis = (
        "\x1f".join(domains) if domains else "name:" + (name or "").strip().casefold()
    )
    company = CompanySignal(
        company_id="co-" + sha256(basis.encode("utf-8")).hexdigest()[:16],
        name=name,
        domains=domains,
    )
    return (
        Employment(company=company, title=_text(_winner_value(resolution, _TITLE))),
    )


def _signals(
    resolution: ClusterResolution,
) -> tuple[tuple[TechSignal, ...], tuple[IntentSignal, ...]]:
    tech: dict[str, TechSignal] = {}
    intent: dict[str, IntentSignal] = {}
    for f in resolution.fields:
        for candidate in (f.winner, *f.agreeing, *f.superseded):
            for item in _flatten(candidate.value):
                if isinstance(item, TechSignal):
                    tech.setdefault(item.label, item)
                elif isinstance(item, IntentSignal):
                    intent.setdefault(item.label, item)
    return (
        tuple(tech[k] for k in sorted(tech)),
        tuple(intent[k] for k in sorted(intent)),
    )


def _flatten(value: Any) -> tuple[object, ...]:
    if isinstance(value, tuple | list):
        return tuple(value)
    return (value,)
