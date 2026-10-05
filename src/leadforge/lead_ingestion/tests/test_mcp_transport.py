"""MCP transport seam with interactive-auth fallback (task 4.3)."""

import ast
from collections.abc import Mapping
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion import mcp_transport as mcp_module
from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    SourceTimedOut,
    SourceTransient,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.mcp_transport import (
    McpInteractiveAuthError,
    McpTransport,
)
from leadforge.lead_ingestion.transport import Transport, TransportResponse

SEARCH = Endpoint(method="POST", path="/v1/people/search", bucket="default")
LOOKUP = Endpoint(method="GET", path="/v1/people/{id}", bucket="default")
ENDPOINTS: Mapping[str, Endpoint] = {"search": SEARCH, "lookup": LOOKUP}
PAYLOAD = {"people": [{"id": "p1"}]}
FALLBACK_PAYLOAD = {"people": [{"id": "from-fallback"}]}


class FakeSession:
    def __init__(self, result: object = PAYLOAD, raises: BaseException | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object:
        self.calls.append((name, arguments))
        if self.raises is not None:
            raise self.raises
        return self.result


class FakeFallback:
    def __init__(self) -> None:
        self.calls = 0

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls += 1
        return TransportResponse(status=200, headers={}, body=FALLBACK_PAYLOAD)


def make(session: FakeSession, fallback: FakeFallback | None = None) -> McpTransport:
    return McpTransport(
        "provider",
        ENDPOINTS,
        session,
        fallback=fallback or FakeFallback(),
        fallback_kind="synthetic",
    )


async def send(t: Transport, endpoint: Endpoint = SEARCH) -> TransportResponse:
    return await t.send(endpoint, params=None, json_body=None, headers={})


# Verifies: specs/lead-source-adapters/requirements.md#20.2
async def test_satisfies_the_port_and_returns_a_transport_response() -> None:
    t: Transport = make(FakeSession())
    assert await send(t) == TransportResponse(status=200, headers={}, body=PAYLOAD)


# Verifies: specs/lead-source-adapters/requirements.md#20.2
async def test_calls_the_tool_named_by_the_endpoint_key_with_params_and_body() -> None:
    session = FakeSession()
    t = make(session)
    await t.send(
        SEARCH, params={"page": 2}, json_body={"q": "x"}, headers={"X-Api-Key": "k"}
    )
    assert session.calls == [("search", {"page": 2, "q": "x"})]


# Verifies: specs/lead-source-adapters/requirements.md#20.2
async def test_body_wins_over_params_on_name_clash_and_headers_are_not_forwarded() -> (
    None
):
    session = FakeSession()
    await make(session).send(
        SEARCH, params={"q": "a"}, json_body={"q": "b"}, headers={"Authorization": "s"}
    )
    assert session.calls == [("search", {"q": "b"})]


# Verifies: specs/lead-source-adapters/requirements.md#20.2
async def test_undeclared_endpoint_is_rejected_before_any_tool_call() -> None:
    session = FakeSession()
    other = Endpoint(method="GET", path="/v1/other", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await send(make(session), other)
    assert session.calls == []


# Verifies: specs/lead-source-adapters/requirements.md#20.5
async def test_interactive_auth_falls_back_and_logs_the_reason() -> None:
    session = FakeSession(raises=McpInteractiveAuthError("browser consent needed"))
    fallback = FakeFallback()
    t = make(session, fallback)
    with capture_logs() as logs:
        resp = await send(t)
    assert resp.body == FALLBACK_PAYLOAD
    assert fallback.calls == 1
    assert len(logs) == 1
    assert logs[0]["event"] == "mcp_interactive_auth_fallback"
    assert logs[0]["log_level"] == "warning"
    assert logs[0]["provider"] == "provider"
    assert logs[0]["fallback"] == "synthetic"
    assert logs[0]["reason"] == "browser consent needed"


# Verifies: specs/lead-source-adapters/requirements.md#20.5
async def test_fallback_is_sticky_so_the_browser_flow_is_not_retried() -> None:
    session = FakeSession(raises=McpInteractiveAuthError("consent"))
    fallback = FakeFallback()
    t = make(session, fallback)
    with capture_logs() as logs:
        await send(t)
        await send(t)
        await send(t, LOOKUP)
    assert len(session.calls) == 1
    assert fallback.calls == 3
    assert len(logs) == 1


# Verifies: specs/lead-source-adapters/requirements.md#20.5
async def test_undeclared_endpoint_still_rejected_after_fallback() -> None:
    t = make(FakeSession(raises=McpInteractiveAuthError("x")))
    await send(t)
    other = Endpoint(method="GET", path="/v1/other", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await send(t, other)


# Verifies: specs/lead-source-adapters/requirements.md#20.5
async def test_non_auth_failures_do_not_trigger_fallback() -> None:
    fallback = FakeFallback()
    t = make(FakeSession(raises=ConnectionError("down")), fallback)
    with pytest.raises(SourceTransient):
        await send(t)
    assert fallback.calls == 0


# Verifies: specs/lead-source-adapters/requirements.md#20.2
async def test_timeout_maps_to_source_timed_out() -> None:
    t = make(FakeSession(raises=TimeoutError()))
    with pytest.raises(SourceTimedOut):
        await send(t)


# Verifies: specs/lead-source-adapters/requirements.md#20.5
def test_fallback_kind_must_be_rest_or_synthetic() -> None:
    with pytest.raises(ValueError, match="fallback_kind"):
        McpTransport(
            "provider",
            ENDPOINTS,
            FakeSession(),
            fallback=FakeFallback(),
            fallback_kind="mcp",  # type: ignore[arg-type]
        )


# Verifies: specs/lead-source-adapters/requirements.md#20.1
def test_module_exposes_no_mcp_sdk_type_above_the_port() -> None:
    tree = ast.parse(Path(mcp_module.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert "mcp" not in imported
