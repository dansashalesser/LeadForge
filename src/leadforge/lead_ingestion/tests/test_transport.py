"""Transport port and REST implementation (task 4.1)."""

import json
from collections.abc import Mapping

import httpx
import pytest
import respx

from leadforge.lead_ingestion import transport as transport_module
from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    SourceTimedOut,
    SourceTransient,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.transport import (
    RestTransport,
    Transport,
    TransportResponse,
)

BASE = "https://api.example.com"
SEARCH = Endpoint(method="POST", path="/v1/people/search", bucket="default")
LOOKUP = Endpoint(method="GET", path="/v1/people/{id}", bucket="default")
ENDPOINTS: Mapping[str, Endpoint] = {"search": SEARCH, "lookup": LOOKUP}


def make(**kw: float) -> RestTransport:
    return RestTransport("provider", BASE, ENDPOINTS, **kw)


# Verifies: specs/lead-source-adapters/requirements.md#20.1
async def test_rest_transport_satisfies_the_port() -> None:
    t: Transport = make()
    assert hasattr(t, "send")
    await make().aclose()


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@respx.mock
async def test_send_carries_request_parts_and_returns_neutral_response() -> None:
    route = respx.post(f"{BASE}/v1/people/search").mock(
        return_value=httpx.Response(200, json={"people": []}, headers={"X-A": "b"})
    )
    t = make()
    resp = await t.send(
        SEARCH, params={"page": 2}, json_body={"q": "x"}, headers={"X-Api-Key": "k"}
    )
    await t.aclose()
    req = route.calls.last.request
    assert req.url.params["page"] == "2"
    assert req.headers["X-Api-Key"] == "k"
    assert json.loads(req.content) == {"q": "x"}
    assert isinstance(resp, TransportResponse)
    assert resp.status == 200
    assert resp.body == {"people": []}
    assert resp.headers["x-a"] == "b"
    assert not isinstance(resp.body, httpx.Response)


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@respx.mock
async def test_non_2xx_is_returned_not_raised() -> None:
    respx.get(f"{BASE}/v1/people/7").mock(
        return_value=httpx.Response(429, text="slow down")
    )
    t = make()
    resp = await t.send(LOOKUP, params={"id": "7"}, json_body=None, headers={})
    await t.aclose()
    assert resp.status == 429
    assert resp.body is None


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@respx.mock
async def test_path_placeholders_are_filled_and_consumed_from_params() -> None:
    route = respx.get(f"{BASE}/v1/people/a%2Fb").mock(return_value=httpx.Response(200))
    t = make()
    await t.send(LOOKUP, params={"id": "a/b", "x": 1}, json_body=None, headers={})
    await t.aclose()
    url = route.calls.last.request.url
    assert "id" not in url.params
    assert url.params["x"] == "1"


async def test_missing_path_placeholder_value_is_rejected() -> None:
    t = make()
    with pytest.raises(ValueError, match="id"):
        await t.send(LOOKUP, params=None, json_body=None, headers={})
    await t.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#20.4
@respx.mock
async def test_every_request_carries_explicit_connect_and_read_timeouts() -> None:
    route = respx.get(f"{BASE}/v1/people/1").mock(return_value=httpx.Response(200))
    t = make(connect_timeout_s=1.5, read_timeout_s=7.0)
    await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    for call in route.calls:
        timeout = call.request.extensions["timeout"]
        assert timeout["connect"] == 1.5
        assert timeout["read"] == 7.0


# Verifies: specs/lead-source-adapters/requirements.md#20.4
async def test_timeouts_have_no_library_default_and_must_be_positive() -> None:
    assert transport_module.DEFAULT_CONNECT_TIMEOUT_S > 0
    assert transport_module.DEFAULT_READ_TIMEOUT_S > 0
    with pytest.raises(ValueError, match="timeout"):
        make(read_timeout_s=0)
    with pytest.raises(ValueError, match="timeout"):
        make(connect_timeout_s=-1)


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@respx.mock
async def test_undeclared_endpoint_is_rejected_before_any_request() -> None:
    route = respx.route().mock(return_value=httpx.Response(200))
    rogue = Endpoint(method="GET", path="/v1/messages/send", bucket="default")
    t = make()
    with pytest.raises(UndeclaredEndpointError) as exc:
        await t.send(rogue, params=None, json_body=None, headers={})
    await t.aclose()
    assert exc.value.provider == "provider"
    assert exc.value.path == "/v1/messages/send"
    assert not route.called


# Verifies: specs/lead-source-adapters/requirements.md#11.1
async def test_same_path_with_different_method_is_undeclared() -> None:
    t = make()
    wrong = Endpoint(method="GET", path=SEARCH.path, bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await t.send(wrong, params=None, json_body=None, headers={})
    await t.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#20.4
@respx.mock
async def test_timeout_maps_to_source_timed_out() -> None:
    respx.get(f"{BASE}/v1/people/1").mock(side_effect=httpx.ReadTimeout("slow"))
    t = make()
    with pytest.raises(SourceTimedOut) as exc:
        await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    assert exc.value.source_name == "provider"


@respx.mock
async def test_connection_failure_maps_to_source_transient() -> None:
    respx.get(f"{BASE}/v1/people/1").mock(side_effect=httpx.ConnectError("down"))
    t = make()
    with pytest.raises(SourceTransient) as exc:
        await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    assert exc.value.status is None


# Verifies: specs/lead-source-adapters/requirements.md#20.1
@respx.mock
async def test_empty_or_non_json_body_yields_none() -> None:
    respx.get(f"{BASE}/v1/people/1").mock(return_value=httpx.Response(204))
    t = make()
    resp = await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    assert resp.body is None


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("value", ["..", ".", ""])
async def test_path_placeholder_cannot_escape_the_declared_path(value: str) -> None:
    t = make()
    with pytest.raises(ValueError, match="path parameter"):
        await t.send(LOOKUP, params={"id": value}, json_body=None, headers={})
    await t.aclose()


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@respx.mock
async def test_redirects_are_not_followed() -> None:
    respx.get(f"{BASE}/v1/people/1").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.test/x"})
    )
    other = respx.get("https://evil.test/x").mock(return_value=httpx.Response(200))
    t = make()
    resp = await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    assert resp.status == 302
    assert not other.called


# Verifies: specs/lead-source-adapters/requirements.md#20.4
@respx.mock
async def test_timeout_text_names_the_endpoint_and_error_type_but_nothing_else() -> (
    None
):
    # A library message may carry a URL, query string or person data: never persisted.
    leak = "jane.doe@acme.com via https://x.test/v1/people/1?email=jane.doe@acme.com"
    respx.get(f"{BASE}/v1/people/1").mock(side_effect=httpx.ReadTimeout(leak))
    t = make()
    with pytest.raises(SourceTimedOut) as exc:
        await t.send(LOOKUP, params={"id": "1"}, json_body=None, headers={})
    await t.aclose()
    text = str(exc.value)
    assert "jane.doe" not in text
    assert "ReadTimeout" in text
    assert "/v1/people/" in text
