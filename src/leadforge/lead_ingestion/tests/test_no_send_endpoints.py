"""No registered adapter can reach a send-capable or write endpoint (19.1)."""

from pathlib import Path
from typing import Any

import httpx
import pytest

import leadforge.lead_ingestion as slice_pkg
from leadforge.lead_ingestion.base_source import Endpoint, RateBucket, RateWindow
from leadforge.lead_ingestion.errors import (
    SendCapableEndpointError,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.send_prohibition import (
    READ_ONLY_POST_ALLOWLIST,
    SEND_PATH_TOKENS,
    assert_no_send_capable_endpoints,
    send_capable_reason,
)
from leadforge.lead_ingestion.structure_guard import (
    find_direct_transport_sends,
    find_network_client_imports,
    find_send_path_literals,
    find_write_verb_uses,
)
from leadforge.lead_ingestion.transport import (
    FixtureTransport,
    RestTransport,
    _fill_path,
)

SLICE_ROOT = Path(slice_pkg.__file__).resolve().parent
SRC_ROOT = SLICE_ROOT.parents[1]
ADAPTERS_ROOT = SLICE_ROOT / "adapters"

# Send, enrol, messaging and write paths in the shapes the providers publish.
SEND_PATHS = (
    "/api/v1/emailer_campaigns",
    "/api/v1/emailer_campaigns/{id}/add_contact_ids",
    "/api/v1/emailer_messages/{id}",
    "/api/v1/sequences/{id}/enroll",
    "/api/v1/contacts/bulk_create",
    "/api/v1/contacts/{id}/update",
    "/api/v1/tasks",
    "/crm/v3/objects/emails",
    "/crm/v3/objects/contacts/batch/create",
    "/crm/v3/objects/contacts/batch/upsert",
    "/crm/v3/objects/contacts/merge",
    "/crm/v3/objects/notes",
    "/crm/v3/objects/{version}/calls",
    "/marketing/v3/emails/transactional/single-send",
    "/marketing/v3/emails/singleSend",
    "/automation/v4/sequences/enrollments",
    "/engagements/v1/engagements",
    "/webhooks/v3/{app}/subscriptions",
    "/v2/campaigns",
    "/v2/campaigns/{id}/recipients",
    "/v2/leads/delete",
    "/v2/leads/import",
    "/messaging/v1/sms",
    "/v1/optout",
    "/v1/unsubscribe",
)

READ_PATHS = (
    "/api/v1/mixed_people/api_search",
    "/api/v1/people/match",
    "/crm/objects/{version}/contacts/search",
    "/crm/objects/{version}/deals/search",
    "/v2/domain-search",
    "/v2/email-finder",
    "/v2/email-verifier",
    "/search",
)

BUCKET = RateBucket(
    name="default",
    windows=(RateWindow(requests=60, per_seconds=60.0),),
    documented=True,
    doc_url="https://example.com/rate-limits",
)


def _registry() -> SourceRegistry:
    return SourceRegistry.discover()


def _declared() -> list[tuple[str, str, Endpoint]]:
    registry = _registry()
    return [
        (name, label, endpoint)
        for name in registry.names()
        for label, endpoint in registry.source_class(name).endpoints.items()
    ]


# --------------------------------------------------------------- the denylist


# Verifies: specs/lead-source-adapters/requirements.md#11.1
# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize("path", SEND_PATHS)
def test_denylist_flags_every_send_enrol_and_write_path(path: str) -> None:
    assert send_capable_reason(path) is not None


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("path", READ_PATHS)
def test_denylist_passes_the_read_only_paths(path: str) -> None:
    assert send_capable_reason(path) is None


# Verifies: specs/lead-source-adapters/requirements.md#11.3
def test_every_denylist_token_is_lower_case_alphanumeric_and_documented() -> None:
    assert all(t.isalnum() and t == t.lower() for t in SEND_PATH_TOKENS)
    assert {"send", "sequence", "campaign", "enroll", "create", "delete"} <= set(
        SEND_PATH_TOKENS
    )


# --------------------------------------------- the real registry, enumerated


# Verifies: specs/lead-source-adapters/requirements.md#11.2
# Verifies: specs/lead-source-adapters/requirements.md#11.3
def test_every_declared_endpoint_of_every_registered_adapter_is_read_only() -> None:
    declared = _declared()

    assert len(_registry().names()) >= 4
    assert declared  # the enumeration is not vacuous
    for name, label, endpoint in declared:
        where = f"{name}.endpoints[{label!r}] {endpoint.path}"
        assert type(endpoint) is Endpoint, where
        assert endpoint.read_only is True, where
        assert endpoint.method in ("GET", "POST"), where
        assert send_capable_reason(endpoint.path) is None, where


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_every_declared_post_is_an_allowlisted_read_only_lookup() -> None:
    posts = {e.path for _, _, e in _declared() if e.method == "POST"}

    assert posts, "expected at least one read-only POST search to be declared"
    assert posts <= set(READ_ONLY_POST_ALLOWLIST)
    # An allowlist entry no adapter declares is a stale permission: remove it.
    assert set(READ_ONLY_POST_ALLOWLIST) <= posts
    assert all(reason.strip() for reason in READ_ONLY_POST_ALLOWLIST.values())


# ------------------------------------ a mutated adapter is rejected, by name


def _build_mutated(path: str) -> None:
    """Build a registered adapter whose endpoints are one send path."""
    registry = _registry()
    name = registry.names()[0]
    base = registry.source_class(name)
    bucket = next(iter(base.rate_limit))
    mutated = type(
        "Mutated",
        (base,),
        {"endpoints": {"send": Endpoint(method="POST", path=path, bucket=bucket)}},
    )
    mutated(DataMode.SYNTHETIC, transport=FixtureTransport(name, {}))


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("path", SEND_PATHS)
def test_an_adapter_declaring_a_send_endpoint_is_rejected_at_construction(
    path: str,
) -> None:
    with pytest.raises(SendCapableEndpointError) as caught:
        _build_mutated(path)

    assert caught.value.path == path


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_the_helper_names_the_provider_and_path() -> None:
    bad = Endpoint(method="POST", path="/v1/sequences", bucket="default")

    with pytest.raises(SendCapableEndpointError) as caught:
        assert_no_send_capable_endpoints("prov", {"x": bad})

    assert "prov" in str(caught.value)
    assert "/v1/sequences" in str(caught.value)


# --------------------------------- the transport refuses, nothing dispatched


class DispatchedError(AssertionError):
    pass


@pytest.fixture
def no_http(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise DispatchedError(f"a request was dispatched: {args!r}")

    monkeypatch.setattr(httpx.AsyncClient, "request", _fail)
    monkeypatch.setattr(httpx.AsyncClient, "send", _fail)


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("path", SEND_PATHS)
@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_every_real_transport_refuses_a_send_path_and_dispatches_nothing(
    path: str, method: str, no_http: None
) -> None:
    for name in _registry().names():
        cls = _registry().source_class(name)
        asked = Endpoint(method=method, path=path, bucket="default")  # type: ignore[arg-type]
        rest = RestTransport(name, "https://provider.example", cls.endpoints)
        fixture = FixtureTransport(name, cls.endpoints)
        try:
            for transport in (rest, fixture):
                with pytest.raises(UndeclaredEndpointError):
                    await transport.send(asked, params=None, json_body=None, headers={})
        finally:
            await rest.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("path", SEND_PATHS[:4])
def test_a_transport_cannot_be_built_over_a_send_endpoint(path: str) -> None:
    endpoints = {"send": Endpoint(method="POST", path=path, bucket="default")}

    with pytest.raises(SendCapableEndpointError):
        RestTransport("prov", "https://provider.example", endpoints)
    with pytest.raises(SendCapableEndpointError):
        FixtureTransport("prov", endpoints)


# ------------------------------------------------------- the static source scan


def _adapters(tmp_path: Path, source: str) -> Path:
    root = tmp_path / "adapters"
    root.mkdir()
    (root / "mod.py").write_text(source)
    return root


# Verifies: specs/lead-source-adapters/requirements.md#11.3
def test_no_adapter_module_names_a_send_path_or_a_write_verb() -> None:
    assert find_send_path_literals(ADAPTERS_ROOT) == []
    assert find_write_verb_uses(ADAPTERS_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#11.3
def test_the_scan_covers_every_adapter_module_including_backends() -> None:
    scanned = {p.relative_to(ADAPTERS_ROOT).parts for p in ADAPTERS_ROOT.rglob("*.py")}

    assert len(scanned) >= 5
    assert any(parts[0] == "search_backends" for parts in scanned)


# Verifies: specs/lead-source-adapters/requirements.md#11.3
@pytest.mark.parametrize(
    "source",
    [
        'P = "/api/v1/emailer_campaigns"\n',
        'E = Endpoint(method="POST", path="/crm/v3/objects/emails", bucket="b")\n',
        'def f(i):\n    return f"/v1/sequences/{i}/enroll"\n',
        'P = ("/v2/" "campaigns")\n',
    ],
)
def test_scan_flags_a_planted_send_path_literal(tmp_path: Path, source: str) -> None:
    root = _adapters(tmp_path, source)

    hits = find_send_path_literals(root)

    assert {h.path for h in hits} == {root / "mod.py"}


# Verifies: specs/lead-source-adapters/requirements.md#11.3
@pytest.mark.parametrize(
    "source",
    [
        "client.put(url)\n",
        "client.patch(url)\n",
        "client.delete(url)\n",
        'client.request("DELETE", url)\n',
        'E = dict(method="PUT")\n',
        'E = {"method": "patch"}\n',
    ],
)
def test_scan_flags_a_planted_write_verb(tmp_path: Path, source: str) -> None:
    root = _adapters(tmp_path, source)

    assert [h.path for h in find_write_verb_uses(root)] == [root / "mod.py"]


# Verifies: specs/lead-source-adapters/requirements.md#11.3
def test_scan_ignores_read_paths_and_prose(tmp_path: Path) -> None:
    root = _adapters(
        tmp_path,
        '"""Search and send nothing: /not a path."""\n'
        'P = "/api/v1/people/match"\nQ = "campaigns are read elsewhere"\n',
    )

    assert find_send_path_literals(root) == []
    assert find_write_verb_uses(root) == []


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_only_the_contract_and_transports_call_transport_send() -> None:
    assert find_direct_transport_sends(SRC_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_scan_flags_a_direct_send_from_an_adapter_or_elsewhere(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src" / "leadforge"
    (src / "lead_ingestion" / "adapters").mkdir(parents=True)
    offender = src / "lead_ingestion" / "adapters" / "mod.py"
    offender.write_text(
        "async def f(t, e):\n"
        "    return await t.send(e, params=None, json_body=None, headers={})\n"
    )
    ok = src / "lead_ingestion" / "transport.py"
    ok.write_text(offender.read_text())

    hits = find_direct_transport_sends(tmp_path / "src")

    assert [h.path for h in hits] == [offender]


# ------------------------------------------------ review: normalisation bypasses


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    "path",
    [
        "/v1/Send",
        "/v1/SEND",
        "/v1/se%6Ed",
        "/v1/se%256Ed",
        "/v1/%73end/",
        "/v1/sequences/",
        "/v1/single_send",
        "/v1/singleSend",
        "/v1/single-send",
        "/v1/sendEmail",
        "/v1/sendemail",
        "/v1/singlesend",
        "/v1/mailer",
        "/v1/emailer_campaigns",
        "/v1/{x}send",
        "/forms/submit/{portal}/{form}",
        "/crm/v3/objects/contacts/batch/archive",
        "/crm/v3/objects/communications",
        "/crm/v3/objects/meetings",
        "/v2/campaigns/{id}/recipients",
        "/v1/{action}",
        "/v1/{send}",
        "/v1/{op}/lookup",
    ],
)
def test_denylist_is_not_bypassed_by_case_encoding_or_joined_words(path: str) -> None:
    assert send_capable_reason(path) is not None


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    "path",
    [
        "/crm/objects/{version}/contacts/search",
        "/v1/{id}/lookup",
        "/v1/sent_count",
        "/v1/address-search",
        "/v1/consent",
        "/v1/email-finder",
        "/v1/domain-search/{domain}",
    ],
)
def test_denylist_keeps_whole_word_precision(path: str) -> None:
    # 'sent', 'address' and 'consent' hold no send word; matching is by word.
    assert send_capable_reason(path) is None


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_a_malformed_percent_escape_is_not_a_crash() -> None:
    assert send_capable_reason("/v1/%zz") is None


# ----------------------------------------- review: POST is default-deny at runtime


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/contacts",
        "/v2/leads",
        "/crm/v3/objects/contacts",
        "/v1/contacts/{id}/touch",
        "/API/V1/PEOPLE/MATCH",
        "/api/v1/people/match/",
    ],
)
def test_a_post_to_a_path_off_the_allowlist_is_refused_at_runtime(path: str) -> None:
    endpoint = Endpoint(method="POST", path=path, bucket="default")

    with pytest.raises(SendCapableEndpointError) as caught:
        assert_no_send_capable_endpoints("prov", {"x": endpoint})

    assert caught.value.path == path
    assert "allowlist" in caught.value.reason


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_a_get_to_an_unlisted_read_path_is_allowed() -> None:
    endpoint = Endpoint(method="GET", path="/v1/people/lookup", bucket="default")

    assert_no_send_capable_endpoints("prov", {"x": endpoint})


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_an_allowlisted_post_is_accepted() -> None:
    path = next(iter(READ_ONLY_POST_ALLOWLIST))
    endpoint = Endpoint(method="POST", path=path, bucket="default")

    assert_no_send_capable_endpoints("prov", {"x": endpoint})


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_an_allowlisted_post_on_a_send_word_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "/v1/sequences/search"
    patched = {**READ_ONLY_POST_ALLOWLIST, path: "reason"}
    monkeypatch.setattr(
        "leadforge.lead_ingestion.send_prohibition.READ_ONLY_POST_ALLOWLIST", patched
    )
    endpoint = Endpoint(method="POST", path=path, bucket="default")

    with pytest.raises(SendCapableEndpointError):
        assert_no_send_capable_endpoints("prov", {"x": endpoint})


# ------------------------------ review: refused when the class is defined


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/v1/sequences/{id}/enroll"),
        ("POST", "/v1/contacts"),
        ("GET", "/v1/send"),
    ],
)
def test_a_subclass_declaring_a_send_endpoint_fails_at_class_definition(
    method: str, path: str
) -> None:
    name = _registry().names()[0]
    base = _registry().source_class(name)
    bucket = next(iter(base.rate_limit))
    endpoint = Endpoint(method=method, path=path, bucket=bucket)  # type: ignore[arg-type]

    with pytest.raises(SendCapableEndpointError) as caught:
        type("Throwaway", (base,), {"endpoints": {"x": endpoint}})

    assert caught.value.provider == name
    assert caught.value.path == path
    assert str(caught.value) == (
        f"[{name}] SendCapableEndpointError: path={path} reason={caught.value.reason}"
    )


# ------------------------------------ review: method override and GET with a body


@pytest.fixture
def real_endpoint() -> tuple[Endpoint, dict[str, Endpoint]]:
    endpoint = Endpoint(method="GET", path="/v1/lookup/{id}", bucket="default")
    return endpoint, {"lookup": endpoint}


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize(
    "header",
    ["X-HTTP-Method-Override", "x-http-method", "X-Method-Override", "X-HTTP-METHOD"],
)
async def test_the_transport_refuses_a_method_override_header(
    header: str, no_http: None, real_endpoint: tuple[Endpoint, dict[str, Endpoint]]
) -> None:
    endpoint, declared = real_endpoint
    transport = RestTransport("prov", "https://provider.example", declared)
    try:
        with pytest.raises(SendCapableEndpointError):
            await transport.send(
                endpoint, params={"id": "1"}, json_body=None, headers={header: "DELETE"}
            )
    finally:
        await transport.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#11.2
async def test_the_transport_refuses_a_get_with_a_body(
    no_http: None, real_endpoint: tuple[Endpoint, dict[str, Endpoint]]
) -> None:
    endpoint, declared = real_endpoint
    transport = RestTransport("prov", "https://provider.example", declared)
    try:
        with pytest.raises(SendCapableEndpointError):
            await transport.send(
                endpoint, params={"id": "1"}, json_body={"a": 1}, headers={}
            )
    finally:
        await transport.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize("value", ["x/send", "../send", "a%2Fb"])
def test_a_path_parameter_value_stays_one_segment(value: str) -> None:
    path, _ = _fill_path("/v1/lookup/{id}", {"id": value})

    assert path.count("/") == 3  # the value's slash is encoded, never a new segment


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize("value", ["", ".", ".."])
def test_a_dot_or_empty_path_parameter_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="invalid path parameter"):
        _fill_path("/v1/lookup/{id}", {"id": value})


# ------------------------------------------ review: the MCP transport is a third door


class _NoCalls:
    async def call_tool(self, name: str, arguments: Any) -> object:
        raise DispatchedError(f"a tool was called: {name}")


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    ("label", "method", "path"),
    [
        ("lookup", "POST", "/v1/sequences/{id}/enroll"),
        ("lookup", "POST", "/v1/contacts"),
        ("send_email", "GET", "/v1/lookup"),
        ("addToSequence", "GET", "/v1/lookup"),
    ],
)
def test_an_mcp_transport_cannot_be_built_over_a_send_tool(
    label: str, method: str, path: str
) -> None:
    from leadforge.lead_ingestion.mcp_transport import McpTransport

    endpoints = {label: Endpoint(method=method, path=path, bucket="default")}  # type: ignore[arg-type]

    with pytest.raises(SendCapableEndpointError):
        McpTransport(
            "prov",
            endpoints,
            _NoCalls(),
            fallback=FixtureTransport("prov", {}),
            fallback_kind="synthetic",
        )


# ------------------------------------------------- review: the static scan, mutated


# Verifies: specs/lead-source-adapters/requirements.md#11.3
@pytest.mark.parametrize(
    "source",
    [
        "from httpx import delete as remove_it\n",
        "from requests import put\n",
        "import requests as r\nr.put(url)\n",
        "f = getattr(client, 'delete')\n",
        "from httpx import Client\nClient().request(method='PATCH', url=u)\n",
    ],
)
def test_scan_flags_write_verbs_in_aliased_and_dynamic_forms(
    tmp_path: Path, source: str
) -> None:
    root = _adapters(tmp_path, source)

    assert [h.path for h in find_write_verb_uses(root)] == [root / "mod.py"]


# Verifies: specs/lead-source-adapters/requirements.md#11.3
@pytest.mark.parametrize(
    "source",
    [
        'P = "/v1/" + "send"\n',
        'P = "/v1/" + "single" + "-send"\n',
        'def f(a):\n    return f"/v1/{a}/send"\n',
        'def f(a):\n    return f"/v1/{a}" + "/enroll"\n',
        'P = "/v1/sin" "gle-send"\n',
    ],
)
def test_scan_folds_string_built_paths(tmp_path: Path, source: str) -> None:
    root = _adapters(tmp_path, source)

    assert [h.path for h in find_send_path_literals(root)] == [root / "mod.py"]


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    "source",
    [
        "import httpx\n",
        "import requests\n",
        "from httpx import AsyncClient\n",
        "import urllib.request\n",
        "from urllib import request\n",
        "from http.client import HTTPSConnection\n",
        "import socket\n",
        "import smtplib\n",
        "import aiohttp as a\n",
        "m = __import__('requests')\n",
        "import importlib\nm = importlib.import_module('httpx')\n",
    ],
)
def test_scan_flags_a_network_client_import_outside_the_transport(
    tmp_path: Path, source: str
) -> None:
    root = tmp_path / "lead_ingestion"
    root.mkdir()
    (root / "mod.py").write_text(source)
    (root / "transport.py").write_text(source)  # the one module that may

    assert [h.path for h in find_network_client_imports(root)] == [root / "mod.py"]


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_the_whole_slice_imports_a_network_client_only_in_the_transport() -> None:
    assert find_network_client_imports(SLICE_ROOT) == []


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_the_client_scan_allows_url_parsing(tmp_path: Path) -> None:
    root = tmp_path / "lead_ingestion"
    root.mkdir()
    (root / "mod.py").write_text(
        "from urllib.parse import quote\nimport urllib.parse\n"
    )

    assert find_network_client_imports(root) == []
