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
  ``person.last_name`` joined by a space (both required), as ``match_keys`` does; a
  masked name (any ``*``, how a provider obfuscates last names) is no name.
* One ``Employment`` at most, from the winning ``company.name`` / ``company.domain``
  and ``person.title``. ``company_id`` is derived from the registrable-domain set
  (task 16.9, ``companies``), and ``domains`` is that set, so ``www.x.com`` and
  ``x.com`` are one id with one content. A Lead whose company has no usable domain gets
  an id derived from its own cluster id, never from the name (no merge by name alone).
  ``is_current`` stays ``None``: no canonical path states it. With no organization name
  or usable domain there is no Employment and the title stays in
  provenance only.
* Signals are evidence, not competing values: every ``TechSignal`` and
  ``IntentSignal`` found in any candidate of any path is carried, one per (kind,
  label), the first in candidate order (path, winner, agreeing, superseded) keeping
  its strength, so Signal Strength never decides anything (24.4). Flat adapter values
  that are not ``Signal`` objects (the flat ``company.technologies`` list) are not
  turned into Signals here.
* The log line of 21.4 is derived from the result (task 16.12, ``merge_log``):
  ``match_keys`` is the cluster's ``merged_by`` (kinds that linked it, strongest first)
  and ``conflicts`` lists, per path with a losing value, the winning source, the number
  of superseded candidates and the rule that decided (``ConflictRule``). Kinds, source
  names, paths and counts only; ``contribution_count`` says whether it was a merge.
* Personal data stays out of ``repr`` and no error text is built from it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from typing import Any

from pydantic import HttpUrl, TypeAdapter, ValidationError

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.companies import company_domains, company_id_for
from leadforge.lead_ingestion.compliance import (
    COMPLIANCE_FLAGS,
    Blocked,
    identities,
    is_flag_set,
)
from leadforge.lead_ingestion.conflicts import (
    ClusterResolution,
    ConflictRule,
    FieldCandidate,
    resolve_conflicts,
)
from leadforge.lead_ingestion.match_keys import MatchKeyKind
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

__all__ = ["ProjectionResult", "ResolvedConflict", "project_lead"]

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
_OPT_OUT = "opt_out"  # both are in ``COMPLIANCE_FLAGS``
_SUPPRESSED = "suppressed"

_EMAIL_ADAPTER: TypeAdapter[str] = TypeAdapter(StrictEmail)
_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)


@dataclass(frozen=True)
class ResolvedConflict:
    """One path whose competing values were resolved; no value, only who and why."""

    canonical_path: str
    winning_source: str
    superseded_count: int
    decided_by: ConflictRule


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
    # Task 16.12 (21.4): Match Key kinds that linked the cluster, strongest first.
    match_keys: tuple[MatchKeyKind, ...] = ()
    # Paths with a losing value, sorted by path.
    conflicts: tuple[ResolvedConflict, ...] = ()
    contribution_count: int = 0
    # Task 19.3 (11.4): sorted names of the sources that set ``opt_out`` or
    # ``suppressed`` on this Lead, in its own cluster or on a linked identity.
    compliance_sources: tuple[str, ...] = ()


def project_lead(
    cluster: IdentityCluster,
    trust_ranks: Mapping[str, int],
    *,
    blocked: Blocked | None = None,
) -> ProjectionResult:
    """Project ``cluster``; the result ignores contribution order.

    ``blocked`` (``compliance.blocked_identities`` over every contribution of the run)
    carries a flag a source set on an identity onto a Lead another source supplied
    (11.4): the report may sit in a cluster of its own, since an unverified address is
    no Match Key.
    """
    members = tuple(_with_canonical_email(c) for c in cluster.contributions)
    resolution = _flagged_first(
        resolve_conflicts(IdentityCluster(cluster.cluster_id, members), trust_ranks)
    )
    sources = sorted(
        {c.source_name for c in members} | set(contributing_sources(resolution))
    )
    reports = _compliance_reports(resolution, members, blocked or {})
    opt_out = any(flag == _OPT_OUT for flag, _ in reports)
    suppressed = any(flag == _SUPPRESSED for flag, _ in reports)
    return ProjectionResult(
        lead=_build_lead(resolution, cluster.cluster_id, opt_out, suppressed),
        contributing_sources=tuple(sources),
        agreement=tuple(
            (f.canonical_path, agreeing_source_count(f)) for f in resolution.fields
        ),
        provenance=provenance_records(resolution),
        negative_evidence=resolution.negative_evidence,
        not_applicable=resolution.not_applicable,
        opt_out=opt_out,
        suppressed=suppressed,
        compliance_sources=tuple(sorted({source for _, source in reports})),
        match_keys=cluster.merged_by,
        conflicts=tuple(
            ResolvedConflict(
                f.canonical_path, f.winner.source_name, len(f.superseded), f.decided_by
            )
            for f in resolution.fields
            if f.decided_by is not None
        ),
        contribution_count=len(members),
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


def _flagged_first(resolution: ClusterResolution) -> ClusterResolution:
    """Make a flag's winner the first source that set it, and never a rank decision.

    A Suppression is not resolved by trust rank (11.4): the candidates that set the flag
    lead, so provenance names the source that flagged the Lead, and the path reports no
    resolved conflict.
    """
    fields = []
    for f in resolution.fields:
        if f.canonical_path not in COMPLIANCE_FLAGS:
            fields.append(f)
            continue
        everyone = (f.winner, *f.agreeing, *f.superseded)
        flagged = [c for c in everyone if is_flag_set(c.value)]
        if not flagged:
            fields.append(replace(f, decided_by=None))
            continue
        winner = flagged[0]
        agreeing = tuple(c for c in flagged[1:] if _same_value(c.value, winner.value))
        kept = {id(winner), *(id(c) for c in agreeing)}
        superseded = tuple(c for c in everyone if id(c) not in kept)
        fields.append(
            replace(
                f,
                winner=winner,
                agreeing=agreeing,
                superseded=superseded,
                decided_by=None,
            )
        )
    return replace(resolution, fields=tuple(fields))


def _same_value(a: object, b: object) -> bool:
    return type(a) is type(b) and a == b


def _compliance_reports(
    resolution: ClusterResolution,
    members: tuple[LeadContribution, ...],
    blocked: Blocked,
) -> frozenset[tuple[str, str]]:
    """``(flag, source_name)`` for every flag set on this Lead; fails closed."""
    reports: set[tuple[str, str]] = set()
    for flag in COMPLIANCE_FLAGS:
        reports.update(
            (flag, c.source_name)
            for c in _candidates(resolution, flag)
            if is_flag_set(c.value)
        )
    for member in members:
        for identity in identities(member):
            reports.update(blocked.get(identity, ()))
    return frozenset(reports)


def _text(value: object) -> str | None:
    if isinstance(value, UntrustedText):
        value = value.value
    if isinstance(value, str) and value.strip():
        return value
    return None


def _build_lead(
    resolution: ClusterResolution, cluster_id: str, opt_out: bool, suppressed: bool
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
        employments=_employments(resolution, cluster_id),
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
    """The winning name; a masked one (any ``*``) is no name, as in ``match_keys``."""
    name = _text(_winner_value(resolution, _FULL_NAME))
    if name is None:
        first = _text(_winner_value(resolution, _FIRST_NAME))
        last = _text(_winner_value(resolution, _LAST_NAME))
        if first is None or last is None:
            return None
        name = f"{first} {last}"
    return None if "*" in name else name


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


def _employments(
    resolution: ClusterResolution, cluster_id: str
) -> tuple[Employment, ...]:
    name = _text(_winner_value(resolution, _COMPANY_NAME))
    domains = tuple(sorted(company_domains(_winner_value(resolution, _COMPANY_DOMAIN))))
    if name is None and not domains:
        return ()
    company = CompanySignal(
        # A company with no usable domain is this Lead's own: never merged by name.
        company_id=company_id_for(domains) if domains else _lead_company_id(cluster_id),
        name=name,
        domains=domains,
    )
    return (
        Employment(company=company, title=_text(_winner_value(resolution, _TITLE))),
    )


def _lead_company_id(cluster_id: str) -> str:
    basis = "no-domain\x1f" + cluster_id
    return "co-" + sha256(basis.encode("utf-8")).hexdigest()[:16]


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
