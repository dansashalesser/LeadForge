"""Every provider's fixtures cover a positive and a negative outcome (task 17.3).

Requirement 5.5. The matrix below names, per fixture file, what running the adapter
over it in synthetic mode (``FixtureTransport``, no socket) must yield. It is enforced
three ways: every registered source's every endpoint, and every capability its
declarations imply (Suppression, technographic targeting, an email verdict), has at
least one positive and one negative case; every JSON fixture the manifest records has a
case and every case names a recorded fixture; and each case's expectation holds when
the adapter really runs.

Expectations come from the requirements, not from the adapter's output: a positive case
returns the data asked for, a negative case was asked and found nothing (no
contribution, or Negative Evidence on a declared surface), and a source with no surface
for a path never reports Negative Evidence there but Not Applicable.

The fixtures are hand-made stand-ins (manifest ``origin`` hand_made, ``unverified``).
"""

import os
import shutil
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import ApolloSource, credits_in
from leadforge.lead_ingestion.adapters.google_search import GoogleSearchSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import (
    TARGET_TERM_PATH_PREFIX,
    BaseLeadSource,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import InvalidAbsenceError
from leadforge.lead_ingestion.fixture_metadata import load_manifest
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    SourceAbsence,
    UntrustedText,
)
from leadforge.lead_ingestion.orchestrator import prune_flagged
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.transport import FixtureTransport

FIXTURES_ROOT = Path(__file__).resolve().parent.parent.parent / "fixtures"
_REGISTRY = SourceRegistry.discover()
SOURCES: Mapping[str, type[BaseLeadSource]] = {
    name: _REGISTRY.source_class(name) for name in _REGISTRY.names()
}
LOOKUP_EMAIL = "ada@example.com"
LOOKUP_DOMAIN = "example.com"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """No case here may reach the network; the fixture transport holds no socket."""

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("an outcome-matrix test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    # Nor may one read a credential: a synthetic source needs none (4.1, 5.2).
    for source in SOURCES.values():
        for variable in source.required_env:
            monkeypatch.delenv(variable, raising=False)


# Verifies: specs/lead-source-adapters/requirements.md#5.2
def test_the_socket_guard_is_active_and_no_credential_is_set() -> None:
    with (
        socket.socket() as probe,
        pytest.raises(AssertionError, match="opened a socket"),
    ):
        probe.connect(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="opened a socket"):
        socket.getaddrinfo("example.com", 443)
    assert not [
        v for source in SOURCES.values() for v in source.required_env if v in os.environ
    ]


class Outcome(StrEnum):
    POSITIVE = "positive"  # the provider returned what the label asks for
    NEGATIVE = "negative"  # asked, and nothing matched / the answer is "no"


@dataclass(frozen=True)
class Run:
    """What one synthetic run over one fixture produced."""

    work: tuple[LeadContribution, ...]
    batch: RawBatch
    contributions: list[LeadContribution]
    events: list[str]
    logs: list[Mapping[str, Any]]


Expect = Callable[[Run], None]


@dataclass(frozen=True)
class Case:
    provider: str
    file: str  # path under fixtures/<provider>/, as the manifest records it
    outcomes: Mapping[str, Outcome]  # endpoint or capability label -> outcome
    expect: Expect

    @property
    def endpoint(self) -> str:
        return PurePosixPath(self.file).stem


def plain(value: object) -> object:
    return value.value if isinstance(value, UntrustedText) else value


def lead_of(**values: object) -> LeadContribution:
    """A work-list lead from canonical path -> value, as an earlier source left it."""
    paths = {path.replace("__", "."): value for path, value in values.items()}
    return LeadContribution(
        source_name="apollo",
        values=paths,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name="apollo",
                data_mode=DataMode.SYNTHETIC,
                fetched_at=datetime(2026, 10, 5, tzinfo=UTC),
                raw_field_path=path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in paths
        ),
    )


def enrichment(*leads: LeadContribution) -> EnrichmentRequest:
    return EnrichmentRequest(kind="enrich", work_list=leads)


# ---------------------------------------------------------------- drivers


def _build(provider: str, transport: Any) -> BaseLeadSource:
    if provider == "apollo":
        return ApolloSource(DataMode.SYNTHETIC, transport=transport)
    if provider == "hubspot":
        return HubSpotSource(DataMode.SYNTHETIC, transport=transport)
    if provider == "hunter":
        return HunterSource(DataMode.SYNTHETIC, transport=transport, environ={})
    if provider == "google_search":
        return GoogleSearchSource(
            DataMode.SYNTHETIC, transport=transport, queries=("example platform jobs",)
        )
    raise AssertionError(f"no driver for provider {provider!r}: add one with its cases")


def _request(provider: str, endpoint: str) -> tuple[SourceRequest, tuple[Any, ...]]:
    """The request that makes the adapter call this endpoint, and its work list."""
    if provider == "apollo" and endpoint == "match":
        work = (lead_of(person__provider_id="apollo-person-1"),)
    elif provider == "hubspot":
        work = (lead_of(email=LOOKUP_EMAIL),)
    elif provider == "hunter" and endpoint == "domain_search":
        work = (lead_of(company__domain=LOOKUP_DOMAIN),)
    elif provider == "hunter" and endpoint == "email_finder":
        work = (
            lead_of(
                person__first_name="Ada",
                person__last_name="Lovelace",
                company__domain=LOOKUP_DOMAIN,
            ),
        )
    elif provider == "hunter":
        work = (lead_of(person__email="ada.lovelace@example.com"),)
    else:
        return SourceRequest(kind="discovery"), ()
    return enrichment(*work), work


def scenario_root(tmp_path: Path, provider: str, file: str) -> Path:
    """The provider's default fixtures, with a variant served for its endpoint."""
    root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES_ROOT / provider, root / provider)
    if "/" in file:
        endpoint = PurePosixPath(file).stem
        shutil.copyfile(
            FIXTURES_ROOT / provider / file, root / provider / f"{endpoint}.json"
        )
    return root


async def run_case(tmp_path: Path, case: Case) -> Run:
    cls = SOURCES[case.provider]
    root = scenario_root(tmp_path, case.provider, case.file)
    transport = cls.build_transport(DataMode.SYNTHETIC, fixtures_root=root)
    assert isinstance(transport, FixtureTransport)  # the substitution, never a socket
    source = _build(case.provider, transport)
    request, work = _request(case.provider, case.endpoint)
    with capture_logs() as logs:
        batch = await source.fetch_raw(request)
        contributions = source.normalize_checked(batch)
    return Run(
        work=work,
        batch=batch,
        contributions=contributions,
        events=[str(entry["event"]) for entry in logs],
        logs=list(logs),
    )


# ----------------------------------------------------------- expectations


def apollo_search_found(run: Run) -> None:
    ids = {c.values["person.provider_id"] for c in run.contributions}
    assert ids == {"apollo-person-1", "apollo-person-2"}
    assert plain(run.contributions[0].values["company.name"]) == "Example Data Corp"
    # 12.8: search returns no email and no phone.
    assert not [k for c in run.contributions for k in c.values if "email" in k]
    assert "apollo_technology_no_matches" not in run.events


def apollo_search_no_match(run: Run) -> None:
    assert run.contributions == []
    unmatched = {
        e["uid"] for e in run.logs if e["event"] == "apollo_technology_no_matches"
    }
    assert unmatched == {"datastax", "cassandra"}  # 12.13: named, never silent


def apollo_match_found(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["person.email"] == LOOKUP_EMAIL
    assert found.values["person.email_status"] == "verified"
    assert credits_in(run.batch) == 1


def apollo_match_none(run: Run) -> None:
    # 12.9: a no-match contributes no lead and no credit.
    assert run.contributions == []
    assert credits_in(run.batch) == 0
    assert "apollo_no_match" in run.events


def hubspot_opted_out(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["crm.contact_exists"] is True
    assert found.values["opt_out"] is True  # 13.7
    assert found.values["suppressed"] is True
    assert found.values["crm.has_open_deal"] is True  # the default deal fixture
    assert prune_flagged(run.work, (found,)) == ()  # the lead leaves the work list


def hubspot_not_opted_out(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["crm.contact_exists"] is True
    assert found.values["opt_out"] is False
    assert found.values["suppressed"] is False
    assert plain(found.values["crm.lifecycle_stage"]) == "lead"
    assert found.absences == ()
    assert prune_flagged(run.work, (found,)) == run.work  # nothing to exclude


def hubspot_not_found(run: Run) -> None:
    (found,) = run.contributions
    assert found.values == {"email": LOOKUP_EMAIL}  # only the question itself
    surfaces = HubSpotSource.answerable_surfaces
    # Every CRM question except the deal search (never made without a contact) was
    # asked, so each is Negative Evidence on its own declared surface (3.1, 13.2).
    asked = set(surfaces) - {"crm.has_open_deal"}
    assert {a.canonical_path for a in found.absences} == asked
    for absence in found.absences:
        assert absence.kind is AbsenceKind.NEGATIVE_EVIDENCE
        assert absence.raw_field_path in surfaces[absence.canonical_path]
    assert "opt_out" not in found.values  # not found is never read as opted out
    assert prune_flagged(run.work, (found,)) == run.work


def hubspot_open_deal(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["crm.has_open_deal"] is True


def hubspot_no_open_deal(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["crm.has_open_deal"] is False  # an answer, not an absence
    assert "crm.has_open_deal" not in {a.canonical_path for a in found.absences}


def hunter_domain_found(run: Run) -> None:
    by_address = {c.values["person.email"]: c for c in run.contributions}
    assert set(by_address) == {
        "ada.lovelace@example.com",
        "grace.hopper@example.com",
    }
    assert (
        by_address["ada.lovelace@example.com"].values["person.email_status"]
        is EmailStatus.VERIFIED
    )
    # 16.2: an address Hunter never verified is never marked verified.
    assert "person.email_status" not in by_address["grace.hopper@example.com"].values


def hunter_domain_empty(run: Run) -> None:
    assert run.contributions == []


def hunter_finder_found(run: Run) -> None:
    (found,) = run.contributions
    assert found.values["person.email"] == "ada.lovelace@example.com"
    assert found.values["person.email_status"] is EmailStatus.VERIFIED


def hunter_finder_not_found(run: Run) -> None:
    # SPEC GAP (1.9): Hunter declares person.email answerable and a finder was asked,
    # so "asked, no match" arguably is Negative Evidence on a lead. The adapter (15.1)
    # contributes nothing and never passes queried paths, so this pins today's
    # behaviour, not the requirement. Changing it needs a decision on which identity
    # the absence attaches to.
    assert run.contributions == []


def verdict(expected: EmailStatus, *, unflagged: bool = False) -> Expect:
    def check(run: Run) -> None:
        (found,) = run.contributions
        assert found.values["person.email"] == "ada.lovelace@example.com"
        assert found.values["person.email_status"] is expected
        if unflagged:  # an answered verification is no restriction: nothing pruned
            assert "suppressed" not in found.values
            assert prune_flagged(run.work, (found,)) == run.work

    return check


def google_found(run: Run) -> None:
    urls = [plain(c.values["company.web_evidence.url"]) for c in run.contributions]
    assert urls == [
        "https://jobs.example.test/senior-data-engineer",
        "https://conf.example.test/talks/platform",
    ]
    assert all(c.absences == () for c in run.contributions)


def google_no_results(run: Run) -> None:
    # No answerable surface is declared, so zero results is no evidence, never an
    # absence (1.9, 14.2); and no Negative Evidence could be constructed for it.
    assert run.contributions == []


P, N = Outcome.POSITIVE, Outcome.NEGATIVE

CASES: tuple[Case, ...] = (
    Case(
        "apollo", "search.json", {"search": P, "target_match": P}, apollo_search_found
    ),
    Case(
        "apollo",
        "no_match/search.json",
        {"search": N, "target_match": N},
        apollo_search_no_match,
    ),
    Case("apollo", "match.json", {"match": P}, apollo_match_found),
    Case("apollo", "no_match/match.json", {"match": N}, apollo_match_none),
    Case(
        "hubspot",
        "contact_search.json",
        {"contact_search": P, "suppression": P},
        hubspot_opted_out,
    ),
    Case(
        "hubspot",
        "not_opted_out/contact_search.json",
        {"contact_search": P, "suppression": N},
        hubspot_not_opted_out,
    ),
    Case(
        "hubspot",
        "not_found/contact_search.json",
        {"contact_search": N, "suppression": N},
        hubspot_not_found,
    ),
    Case("hubspot", "deal_search.json", {"deal_search": P}, hubspot_open_deal),
    Case(
        "hubspot",
        "no_open_deals/deal_search.json",
        {"deal_search": N},
        hubspot_no_open_deal,
    ),
    Case("hunter", "domain_search.json", {"domain_search": P}, hunter_domain_found),
    Case(
        "hunter",
        "no_emails/domain_search.json",
        {"domain_search": N},
        hunter_domain_empty,
    ),
    Case("hunter", "email_finder.json", {"email_finder": P}, hunter_finder_found),
    Case(
        "hunter",
        "not_found/email_finder.json",
        {"email_finder": N},
        hunter_finder_not_found,
    ),
    Case(
        "hunter",
        "email_verifier.json",
        {"email_verifier": P, "email_status": P, "suppression": N},
        verdict(EmailStatus.VERIFIED, unflagged=True),
    ),
    Case(
        "hunter",
        "invalid/email_verifier.json",
        {"email_verifier": P, "email_status": N},
        verdict(EmailStatus.INVALID),
    ),
    Case(
        "hunter",
        "accept_all/email_verifier.json",
        {"email_verifier": N, "email_status": N},
        verdict(EmailStatus.ACCEPT_ALL),
    ),
    Case(
        "hunter",
        "unknown/email_verifier.json",
        {"email_verifier": N, "email_status": N},
        verdict(EmailStatus.UNKNOWN),
    ),
    Case("google_search", "search.json", {"search": P}, google_found),
    Case("google_search", "no_results/search.json", {"search": N}, google_no_results),
)


# ------------------------------------------------------------ the matrix


def required_labels(source: type[BaseLeadSource]) -> set[str]:
    """Every endpoint, and every capability the source's declarations imply."""
    labels = set(source.endpoints)
    if source.yields_suppression:
        labels.add("suppression")
    if any(p.startswith(TARGET_TERM_PATH_PREFIX) for p in source.answerable_surfaces):
        labels.add("target_match")
    if "person.email_status" in source.answerable_surfaces:
        labels.add("email_status")
    return labels


# Outcomes no fixture can show, each with where it is proved instead. Hunter's
# Suppression positive is an HTTP 451 (16.6), and ``FixtureTransport`` serves every
# fixture as a 200, so no fixture file can carry it. It is proved over scripted 451s in
# test_hunter_source.py (the 451 tests) and test_suppression_end_to_end.py (a 451
# prunes the person before Apollo). Added 2026-10-06 when Hunter declared Suppression.
NOT_FIXTURE_SHAPED: frozenset[str] = frozenset({"hunter:suppression:positive"})


def missing_outcomes(
    cases: tuple[Case, ...], sources: Mapping[str, type[BaseLeadSource]]
) -> list[str]:
    """``<provider>:<label>:<outcome>`` for each positive or negative nobody covers."""
    missing: list[str] = []
    for provider in sorted(sources):
        mine = [c for c in cases if c.provider == provider]
        for label in sorted(required_labels(sources[provider])):
            for outcome in Outcome:
                if f"{provider}:{label}:{outcome}" in NOT_FIXTURE_SHAPED:
                    continue
                if not any(c.outcomes.get(label) is outcome for c in mine):
                    missing.append(f"{provider}:{label}:{outcome}")
    return missing


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_every_endpoint_and_capability_has_a_positive_and_a_negative_case() -> None:
    assert missing_outcomes(CASES, SOURCES) == []


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_the_matrix_flags_a_provider_that_lacks_a_negative_or_a_positive() -> None:
    without_negative = tuple(
        c
        for c in CASES
        if not (c.provider == "hunter" and c.file == "no_emails/domain_search.json")
    )
    assert missing_outcomes(without_negative, SOURCES) == [
        "hunter:domain_search:negative"
    ]
    without_suppression_negative = tuple(
        c
        for c in CASES
        if not (
            c.provider == "hubspot"
            and c.outcomes.get("suppression") is Outcome.NEGATIVE
        )
    )
    assert missing_outcomes(without_suppression_negative, SOURCES) == [
        "hubspot:contact_search:negative",
        "hubspot:suppression:negative",
    ]
    assert missing_outcomes((), {"apollo": SOURCES["apollo"]}) == [
        f"apollo:{label}:{outcome}"
        for label in ("match", "search", "target_match")
        for outcome in Outcome
    ]


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_a_case_labels_only_endpoints_and_capabilities_its_source_declares() -> None:
    for case in CASES:
        assert set(case.outcomes) <= required_labels(SOURCES[case.provider]), case.file
        assert case.endpoint in SOURCES[case.provider].endpoints, case.file


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_every_recorded_fixture_has_a_case_and_every_case_a_recorded_fixture() -> None:
    recorded = {
        (provider, record.file)
        for provider in SOURCES
        for record in load_manifest(FIXTURES_ROOT / provider).fixtures
        if record.endpoint is not None
    }
    assert {(c.provider, c.file) for c in CASES} == recorded
    assert len(CASES) == len(recorded)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
def test_every_outcome_variant_is_a_flagged_hand_made_stand_in() -> None:
    variants = [
        (provider, record)
        for provider in SOURCES
        for record in load_manifest(FIXTURES_ROOT / provider).fixtures
        if "/" in record.file
    ]
    assert variants
    for provider, record in variants:
        assert record.origin.value == "hand_made", (provider, record.file)
        # Only the provider-facts check of 2026-10-06 may mark a variant verified
        # (pinned per file in test_provider_plan_limits.py).
        if record.schema_status.value == "verified":
            assert str(record.schema_verified_on) == "2026-10-06", record.file
        else:
            assert record.schema_verified_on is None, (provider, record.file)
        assert record.redaction is None
        assert "not a capture" in record.note


# Verifies: specs/lead-source-adapters/requirements.md#5.5
@pytest.mark.parametrize("case", CASES, ids=[f"{c.provider}/{c.file}" for c in CASES])
async def test_each_fixture_yields_its_outcome_in_synthetic_mode(
    tmp_path: Path, case: Case
) -> None:
    case.expect(await run_case(tmp_path, case))


# Verifies: specs/lead-source-adapters/requirements.md#5.2
@pytest.mark.parametrize("case", CASES, ids=[f"{c.provider}/{c.file}" for c in CASES])
async def test_each_case_runs_with_no_socket_and_no_credential(
    tmp_path: Path, case: Case
) -> None:
    # The autouse guard refuses any socket; a synthetic source is built with no
    # environment, so a case that needed a credential or the network would fail here.
    run = await run_case(tmp_path, case)
    assert run.batch.source_name == case.provider


# Verifies: specs/lead-source-adapters/requirements.md#2.8
def test_a_source_without_a_term_vocabulary_reports_not_applicable_not_no_match() -> (
    None
):
    terms = sorted(
        {
            path.removeprefix(TARGET_TERM_PATH_PREFIX)
            for source in SOURCES.values()
            for path in source.answerable_surfaces
            if path.startswith(TARGET_TERM_PATH_PREFIX)
        }
    )
    assert terms
    applicable = not_applicable = 0
    for name, cls in SOURCES.items():
        source = cls(
            DataMode.SYNTHETIC, transport=cls.build_transport(DataMode.SYNTHETIC)
        )
        for term in terms:
            absence = source.target_term_absence(term)
            path = f"{TARGET_TERM_PATH_PREFIX}{term}"
            if path in cls.answerable_surfaces:
                assert absence is None, (name, term)
                applicable += 1
                continue
            assert absence is not None, (name, term)
            assert absence.kind is AbsenceKind.NOT_APPLICABLE
            assert absence.raw_field_path is None
            assert source.validate_absence(absence) == absence
            # With no surface for the path, Negative Evidence is refused outright.
            with pytest.raises(InvalidAbsenceError):
                source.validate_absence(
                    SourceAbsence(
                        canonical_path=path,
                        source_name=name,
                        kind=AbsenceKind.NEGATIVE_EVIDENCE,
                        raw_field_path="anything",
                    )
                )
            not_applicable += 1
    assert applicable
    assert not_applicable


# Verifies: specs/lead-source-adapters/requirements.md#5.5
def test_hunters_suppression_still_needs_a_fixture_negative() -> None:
    # Only the 451 positive is exempt (NOT_FIXTURE_SHAPED); the negative is a fixture.
    without = tuple(
        c for c in CASES if not (c.provider == "hunter" and "suppression" in c.outcomes)
    )
    missing = missing_outcomes(without, {"hunter": SOURCES["hunter"]})
    assert [m for m in missing if ":suppression:" in m] == [
        "hunter:suppression:negative"
    ]
    assert {"hunter:suppression:positive"} == NOT_FIXTURE_SHAPED
