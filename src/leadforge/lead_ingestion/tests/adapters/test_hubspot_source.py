"""HubSpot Source adapter: the free CRM-state lookup on the date-versioned path (13.1).

``fixtures/hubspot/*.json`` are hand-made STAND-INS shaped after HubSpot's documented
search response, not captured from a real portal.
"""

import json
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from leadforge.lead_ingestion.adapters import hubspot as hubspot_module
from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.base_source import (
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
    enrichment_order,
)
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    NormalizationError,
    SourceError,
    SourceRateLimited,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    SourceAbsence,
)
from leadforge.lead_ingestion.normalizer import unmapped_raw_paths
from leadforge.lead_ingestion.orchestrator import prune_flagged
from leadforge.lead_ingestion.transport import (
    FixtureTransport,
    RestTransport,
    TransportResponse,
    _fill_path,
)

CONTACT_PATH = "/crm/objects/{version}/contacts/search"
DEAL_PATH = "/crm/objects/{version}/deals/search"
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "hubspot"
TOKEN = "pat-test-token-not-real"
VERSION = "2027-03"
ENV = {"HUBSPOT_ACCESS_TOKEN": TOKEN, "HUBSPOT_API_VERSION": VERSION}


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may reach the network; a scripted or fixture transport is used."""

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a HubSpot test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


Responder = Callable[[Endpoint, Mapping[str, object]], TransportResponse]


class Scripted:
    """A transport that records every call and answers from ``responder``."""

    def __init__(self, responder: Responder) -> None:
        self.responder = responder
        self.calls: list[
            tuple[
                Endpoint, Mapping[str, object], Mapping[str, object], Mapping[str, str]
            ]
        ] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append(
            (endpoint, dict(params or {}), dict(json_body or {}), dict(headers))
        )
        return self.responder(endpoint, json_body or {})


def ok(body: object) -> TransportResponse:
    return TransportResponse(status=200, headers={}, body=body)


def contact(
    contact_id: str = "501",
    *,
    optout: str | None = "false",
    stage: str | None = "lead",
    owner: str | None = "9001",
    activity: str | None = "2026-09-20T14:05:00.000Z",
) -> dict[str, object]:
    return {
        "id": contact_id,
        "properties": {
            "hs_email_optout": optout,
            "lifecyclestage": stage,
            "hubspot_owner_id": owner,
            "notes_last_updated": activity,
        },
    }


def answers(contacts: list[dict[str, object]], open_deals: int = 0) -> Responder:
    def respond(endpoint: Endpoint, _: Mapping[str, object]) -> TransportResponse:
        if endpoint.path == CONTACT_PATH:
            return ok({"total": len(contacts), "results": contacts})
        return ok({"total": open_deals, "results": []})

    return respond


def live(
    transport: Scripted, *, environ: Mapping[str, str] | None = None
) -> HubSpotSource:
    return HubSpotSource(
        DataMode.LIVE, transport=transport, environ=ENV if environ is None else environ
    )


def lead(email: str | None, *, source: str = "apollo") -> LeadContribution:
    values: dict[str, object] = {}
    provenance: tuple[FieldProvenance, ...] = ()
    if email is not None:
        values["email"] = email
        provenance = (
            FieldProvenance(
                canonical_path="email",
                source_name=source,
                data_mode=DataMode.LIVE,
                fetched_at=datetime(2026, 10, 5, tzinfo=UTC),
                raw_field_path="email",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            ),
        )
    return LeadContribution(source_name=source, values=values, provenance=provenance)


def request(*emails: str | None) -> EnrichmentRequest:
    return EnrichmentRequest(
        kind="enrichment", work_list=tuple(lead(e) for e in emails)
    )


async def contributions(
    source: HubSpotSource, req: EnrichmentRequest
) -> list[LeadContribution]:
    return source.normalize_checked(await source.fetch_raw(req))


# Verifies: specs/lead-source-adapters/requirements.md#13.1
def test_declares_a_free_suppression_bearing_enrichment_only_source() -> None:
    assert HubSpotSource.name == "hubspot"
    assert HubSpotSource.capabilities == frozenset({Capability.ENRICH})
    assert HubSpotSource.cost_class is CostClass.FREE
    assert HubSpotSource.charge_unit is ChargeUnit.PER_LEAD
    assert HubSpotSource.yields_suppression is True
    assert HubSpotSource.required_env == ("HUBSPOT_ACCESS_TOKEN", "HUBSPOT_API_VERSION")
    assert HubSpotSource.target_vocabulary == {}
    # Free and suppression-bearing: runs before Apollo's paid match (ADR-0002).
    assert enrichment_order([ApolloSource, HubSpotSource]) == [  # type: ignore[type-var]
        HubSpotSource,
        ApolloSource,
    ]


# Verifies: specs/lead-source-adapters/requirements.md#13.3
def test_endpoints_are_read_only_and_on_the_version_placeholder_path_only() -> None:
    paths = {(e.method, e.path) for e in HubSpotSource.endpoints.values()}
    assert paths == {("POST", CONTACT_PATH), ("POST", DEAL_PATH)}
    assert all(e.read_only is True for e in HubSpotSource.endpoints.values())
    assert all("/v3/" not in e.path for e in HubSpotSource.endpoints.values())


# Verifies: specs/lead-source-adapters/requirements.md#13.4
def test_search_bucket_is_declared_as_five_per_second_documented() -> None:
    bucket = HubSpotSource.rate_limit["search"]
    assert [(w.requests, w.per_seconds) for w in bucket.windows] == [
        (5, 1.0),
        (1, 0.2),  # even spacing, so no one second ever holds six (13.4)
    ]
    assert bucket.documented is True
    assert {e.bucket for e in HubSpotSource.endpoints.values()} == {"search"}


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_sends_the_private_app_token_as_a_bearer_token_only() -> None:
    transport = Scripted(answers([]))
    await live(transport).fetch_raw(request("ada@example.com"))
    headers = transport.calls[0][3]
    assert headers == {"authorization": f"Bearer {TOKEN}"}
    for _, params, _, _ in transport.calls:
        assert "hapikey" not in params
        assert TOKEN not in json.dumps(params)


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_live_run_without_the_token_fails_naming_the_variable_only() -> None:
    transport = Scripted(answers([]))
    source = live(transport, environ={"HUBSPOT_API_VERSION": VERSION})
    with pytest.raises(MissingCredentialError) as caught:
        await source.fetch_raw(request("ada@example.com"))
    assert caught.value.missing == ("HUBSPOT_ACCESS_TOKEN",)
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#13.3
async def test_the_api_version_comes_from_configuration_into_the_path() -> None:
    transport = Scripted(answers([contact()], open_deals=1))
    await live(transport).fetch_raw(request("ada@example.com"))
    assert len(transport.calls) == 2
    for endpoint, params, _, _ in transport.calls:
        assert params["version"] == VERSION
        path, query = _fill_path(endpoint.path, params)
        assert path.startswith(f"/crm/objects/{VERSION}/")
        assert not path.startswith("/crm/v3/")
        assert query == {}


# Verifies: specs/lead-source-adapters/requirements.md#13.3
async def test_changing_the_configured_version_changes_the_path_without_code() -> None:
    transport = Scripted(answers([]))
    env = {**ENV, "HUBSPOT_API_VERSION": "2031-01"}
    await live(transport, environ=env).fetch_raw(request("ada@example.com"))
    path, _ = _fill_path(transport.calls[0][0].path, transport.calls[0][1])
    assert path == "/crm/objects/2031-01/contacts/search"


# Verifies: specs/lead-source-adapters/requirements.md#13.3
def test_the_module_holds_no_version_literal_and_no_legacy_path() -> None:
    text = Path(hubspot_module.__file__).read_text(encoding="utf-8")
    assert "2026-09" not in text
    assert "/crm/v3" not in text


# Verifies: specs/lead-source-adapters/requirements.md#13.3
@pytest.mark.parametrize(
    "bad", ["v3", "2026-9", "2026-09/../v3", "latest", "2026-09-01", "2026-13"]
)
async def test_a_version_that_is_not_a_year_month_is_refused_without_echoing_it(
    bad: str,
) -> None:
    transport = Scripted(answers([]))
    source = live(transport, environ={**ENV, "HUBSPOT_API_VERSION": bad})
    with pytest.raises(SourceError) as caught:
        await source.fetch_raw(request("ada@example.com"))
    assert "HUBSPOT_API_VERSION" in str(caught.value)
    assert bad not in str(caught.value)
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#13.3
async def test_a_blank_version_is_a_missing_credential_naming_the_variable() -> None:
    transport = Scripted(answers([]))
    source = live(transport, environ={**ENV, "HUBSPOT_API_VERSION": "  "})
    with pytest.raises(MissingCredentialError) as caught:
        await source.fetch_raw(request("ada@example.com"))
    assert caught.value.missing == ("HUBSPOT_API_VERSION",)


# Verifies: specs/lead-source-adapters/requirements.md#13.1
async def test_synthetic_run_needs_no_credentials_and_sends_none() -> None:
    transport = Scripted(answers([]))
    source = HubSpotSource(DataMode.SYNTHETIC, transport=transport, environ={})
    await source.fetch_raw(request("ada@example.com"))
    assert transport.calls[0][3] == {}


# Verifies: specs/lead-source-adapters/requirements.md#13.8
def test_live_transport_is_rest_over_https_and_the_module_has_no_mcp_or_browser() -> (
    None
):
    assert HubSpotSource.base_url == "https://api.hubapi.com"
    assert isinstance(HubSpotSource.build_transport(DataMode.LIVE), RestTransport)
    text = Path(hubspot_module.__file__).read_text(encoding="utf-8").lower()
    assert "mcp_transport" not in text
    assert "mcptransport" not in text
    assert "webbrowser" not in text
    assert "oauth" not in text


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_one_contact_lookup_by_email_per_distinct_lead_email() -> None:
    transport = Scripted(answers([]))
    await live(transport).fetch_raw(
        request("Ada@Example.com", "ada@example.com ", None, "bob@example.com")
    )
    bodies = [c[2] for c in transport.calls if c[0].path == CONTACT_PATH]
    assert [b["filterGroups"] for b in bodies] == [
        [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}]
        for email in ("ada@example.com", "bob@example.com")
    ]
    wanted = {"lifecyclestage", "hubspot_owner_id", "notes_last_updated"}
    assert all(
        wanted | {"hs_email_optout"} <= set(b["properties"])  # type: ignore[call-overload]
        for b in bodies
    )


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_open_deal_presence_is_a_search_for_unclosed_deals_of_the_contact() -> (
    None
):
    transport = Scripted(answers([contact("501")], open_deals=2))
    await live(transport).fetch_raw(request("ada@example.com"))
    endpoint, _, body, _ = transport.calls[1]
    assert endpoint.path == DEAL_PATH
    assert body["filterGroups"] == [
        {
            "filters": [
                {
                    "propertyName": "associations.contact",
                    "operator": "EQ",
                    "value": "501",
                },
                {"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"},
            ]
        }
    ]


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_every_search_is_bounded_and_never_pages() -> None:
    transport = Scripted(answers([contact("501")]))
    await live(transport).fetch_raw(request("ada@example.com"))
    limits = {c[0].path: c[2]["limit"] for c in transport.calls}
    assert limits == {
        CONTACT_PATH: hubspot_module.MAX_CONTACTS_PER_LOOKUP,
        DEAL_PATH: 1,
    }
    assert hubspot_module.MAX_CONTACTS_PER_LOOKUP <= 200  # the search page cap
    assert all("after" not in c[2] for c in transport.calls)


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_no_email_makes_no_call_and_a_discovery_request_is_refused() -> None:
    transport = Scripted(answers([]))
    source = live(transport)
    batch = await source.fetch_raw(request(None))
    assert transport.calls == []
    assert source.normalize(batch) == []
    with pytest.raises(SourceError, match="enrichment"):
        await source.fetch_raw(SourceRequest(kind="discovery"))
    assert transport.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_a_second_fetch_repeats_no_lookup_and_no_record() -> None:
    # Follow-up fu2: an answer a returned batch carried is emitted once per run (a
    # failed fetch returns no batch, so a retry still emits; see the echo tests).
    transport = Scripted(answers([contact()]))
    source = live(transport)
    first = await source.fetch_raw(request("ada@example.com"))
    calls = len(transport.calls)
    second = await source.fetch_raw(request("ada@example.com"))
    assert len(transport.calls) == calls
    assert len(first.payload["lookups"]) == 1
    assert second.payload == {"lookups": []}


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_contributes_every_crm_state_signal_with_provenance() -> None:
    transport = Scripted(answers([contact(stage="customer")], open_deals=3))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values == {
        "email": "ada@example.com",
        "crm.contact_exists": True,
        "crm.lifecycle_stage": "customer",
        "crm.owner": "9001",
        "crm.last_activity_date": datetime(2026, 9, 20, 14, 5, tzinfo=UTC),
        "crm.has_open_deal": True,
        "opt_out": False,
        "suppressed": False,
    }
    assert {p.canonical_path for p in found.provenance} == set(found.values)
    assert all(p.source_name == "hubspot" and not p.untrusted for p in found.provenance)
    assert found.absences == ()


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_no_open_deal_is_false_not_absent() -> None:
    transport = Scripted(answers([contact()], open_deals=0))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values["crm.has_open_deal"] is False


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_an_opted_out_contact_sets_both_compliance_flags() -> None:
    transport = Scripted(answers([contact(optout="true")]))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values["opt_out"] is True
    assert found.values["suppressed"] is True


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_an_opted_out_contact_leaves_the_work_list() -> None:
    transport = Scripted(answers([contact(optout="true")]))
    work = request("ada@example.com", "bob@example.com")
    source = live(transport)
    (report,) = await contributions(source, request("ada@example.com"))
    kept = prune_flagged(work.work_list, (report,))
    assert [c.values["email"] for c in kept] == ["bob@example.com"]


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_one_opted_out_duplicate_contact_is_enough_to_flag_the_lead() -> None:
    transport = Scripted(
        answers([contact("1", optout="false"), contact("2", optout="true")])
    )
    found = await contributions(live(transport), request("ada@example.com"))
    assert [c.values["opt_out"] for c in found] == [False, True]
    work = request("ada@example.com")
    assert prune_flagged(work.work_list, tuple(found)) == ()


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_an_email_unknown_to_hubspot_records_negative_evidence_not_a_flag() -> (
    None
):
    transport = Scripted(answers([]))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values == {"email": "ada@example.com"}
    assert {a.canonical_path for a in found.absences} == {
        "crm.contact_exists",
        "crm.lifecycle_stage",
        "crm.owner",
        "crm.last_activity_date",
        "opt_out",
        "suppressed",
    }  # no deal search was made, so "no open deal" was never asked
    assert all(isinstance(a, SourceAbsence) for a in found.absences)
    assert all(a.kind is AbsenceKind.NEGATIVE_EVIDENCE for a in found.absences)
    assert len(transport.calls) == 1  # no contact, so no deal search


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_blank_properties_are_absent_not_empty_values() -> None:
    transport = Scripted(
        answers([contact(stage="", owner=None, activity=None, optout=None)])
    )
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert "crm.lifecycle_stage" not in found.values
    assert "crm.owner" not in found.values
    assert "crm.last_activity_date" not in found.values
    assert "opt_out" not in found.values
    assert found.values["crm.contact_exists"] is True


# Verifies: specs/lead-source-adapters/requirements.md#13.2
@pytest.mark.parametrize(
    ("props", "path"),
    [
        ({"notes_last_updated": "yesterday"}, "notes_last_updated"),
        ({"lifecyclestage": 3}, "lifecyclestage"),
    ],
)
async def test_a_malformed_property_is_a_normalization_error_naming_paths_only(
    props: dict[str, object], path: str
) -> None:
    record = contact()
    record["properties"] = {**record["properties"], **props}  # type: ignore[dict-item]
    transport = Scripted(answers([record]))
    source = live(transport)
    batch = await source.fetch_raw(request("ada@example.com"))
    with pytest.raises(NormalizationError) as caught:
        source.normalize(batch)
    assert path in str(caught.value)
    assert "maybe" not in str(caught.value)
    assert "yesterday" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#13.7
@pytest.mark.parametrize("raw", ["maybe", "yes", "TRUE", "1"])
async def test_an_opt_out_value_that_cannot_be_read_fails_closed(raw: str) -> None:
    """A suppression signal HubSpot sent but we cannot parse is a suppression (11.4)."""
    transport = Scripted(answers([contact(optout=raw)]))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values["opt_out"] is True
    assert found.values["suppressed"] is True


# Verifies: specs/lead-source-adapters/requirements.md#13.7
@pytest.mark.parametrize("raw", ["FALSE", " False ", "no", "0"])
async def test_an_opt_out_value_that_says_no_is_not_an_opt_out(raw: str) -> None:
    """The one reading of a flag (compliance.is_flag_set): "no" in any case is no."""
    transport = Scripted(answers([contact(optout=raw)]))
    (found,) = await contributions(live(transport), request("ada@example.com"))
    assert found.values["opt_out"] is False
    assert found.values["suppressed"] is False


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_a_response_of_the_wrong_shape_is_a_normalization_error() -> None:
    transport = Scripted(lambda _e, _b: ok({"unexpected": True}))
    with pytest.raises(NormalizationError):
        await live(transport).fetch_raw(request("ada@example.com"))


# Verifies: specs/lead-source-adapters/requirements.md#13.2
async def test_the_fixtures_run_end_to_end_with_no_network_and_nothing_unmapped() -> (
    None
):
    transport = FixtureTransport("hubspot", HubSpotSource.endpoints)
    source = HubSpotSource(DataMode.SYNTHETIC, transport=transport)
    raw = await source.fetch_raw(request("ada@example.com"))
    (found,) = source.normalize_checked(raw)
    assert found.values["opt_out"] is True
    assert found.values["suppressed"] is True
    assert found.values["crm.has_open_deal"] is True
    assert found.values["crm.lifecycle_stage"] == "customer"
    assert found.values["crm.owner"] == "9001"
    payload = raw.payload
    assert isinstance(payload, Mapping)
    record = payload["lookups"][0]["contacts"][0]
    shaped = {"lookup": "ada@example.com", **record}
    assert unmapped_raw_paths(shaped, HubSpotSource.RULES, HubSpotSource.IGNORED) == []
    # The fixture directory holds exactly the two hand-made stand-ins this reads
    # (plus manifest.json, the provenance record, task 17.1).
    assert sorted(
        p.name for p in FIXTURE_DIR.glob("*.json") if p.name != "manifest.json"
    ) == [
        "contact_search.json",
        "deal_search.json",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#13.2
def test_a_raw_batch_of_the_wrong_shape_is_refused() -> None:
    source = HubSpotSource(
        DataMode.SYNTHETIC, transport=Scripted(answers([])), environ={}
    )
    with pytest.raises(NormalizationError):
        source.normalize(RawBatch(source_name="hubspot", payload={"nope": []}))


# Verifies: specs/lead-source-adapters/requirements.md#13.1
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, SourceUnauthorized),
        (403, SourceUnauthorized),
        (429, SourceRateLimited),
        (503, SourceTransient),
    ],
)
async def test_error_statuses_map_to_error_types_without_body_or_token(
    status: int, expected: type[SourceError]
) -> None:
    body = {"message": "secret person ada@example.com", "policyName": "SECONDLY"}
    transport = Scripted(lambda _e, _b: TransportResponse(status, {}, body))
    with pytest.raises(expected) as caught:
        await live(transport).fetch_raw(request("ada@example.com"))
    text = str(caught.value)
    assert "ada@example.com" not in text
    assert TOKEN not in text
    assert "secret" not in text
