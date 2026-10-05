"""Fixture transport for synthetic mode (task 4.2)."""

import ast
import json
import socket
from collections.abc import Mapping
from pathlib import Path

import pytest

from leadforge.lead_ingestion import transport as transport_module
from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import FixtureSchemaError, UndeclaredEndpointError
from leadforge.lead_ingestion.transport import (
    FixtureTransport,
    Transport,
    TransportResponse,
)

SEARCH = Endpoint(method="POST", path="/v1/people/search", bucket="default")
LOOKUP = Endpoint(method="GET", path="/v1/people/{id}", bucket="default")
ENDPOINTS: Mapping[str, Endpoint] = {"search": SEARCH, "lookup": LOOKUP}
PAYLOAD = {"people": [{"id": "p1"}]}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "provider").mkdir()
    (tmp_path / "provider" / "search.json").write_text(json.dumps(PAYLOAD))
    return tmp_path


def make(root: Path) -> FixtureTransport:
    return FixtureTransport("provider", ENDPOINTS, fixtures_root=root)


async def send(t: Transport, endpoint: Endpoint) -> TransportResponse:
    return await t.send(endpoint, params=None, json_body=None, headers={})


# Verifies: specs/lead-source-adapters/requirements.md#5.1
async def test_returns_stored_json_for_the_endpoint_through_the_port(
    root: Path,
) -> None:
    t: Transport = make(root)
    resp = await send(t, SEARCH)
    assert resp == TransportResponse(status=200, headers={}, body=PAYLOAD)


# Verifies: specs/lead-source-adapters/requirements.md#5.2
async def test_ignores_request_parts_so_callers_use_one_path(root: Path) -> None:
    t = make(root)
    resp = await t.send(
        SEARCH, params={"page": 2}, json_body={"q": "x"}, headers={"X-Api-Key": "k"}
    )
    assert resp.body == PAYLOAD


# Verifies: specs/lead-source-adapters/requirements.md#5.1
async def test_undeclared_endpoint_is_rejected_like_rest(root: Path) -> None:
    other = Endpoint(method="GET", path="/v1/other", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await send(make(root), other)


# Verifies: specs/lead-source-adapters/requirements.md#5.1
async def test_declared_path_with_other_method_is_rejected(root: Path) -> None:
    wrong = Endpoint(method="GET", path="/v1/people/search", bucket="default")
    with pytest.raises(UndeclaredEndpointError):
        await send(make(root), wrong)


# Verifies: specs/lead-source-adapters/requirements.md#5.4
async def test_missing_fixture_names_provider_and_file(root: Path) -> None:
    with pytest.raises(FixtureSchemaError) as info:
        await send(make(root), LOOKUP)
    assert info.value.provider == "provider"
    assert info.value.field == "provider/lookup.json"


# Verifies: specs/lead-source-adapters/requirements.md#5.4
async def test_unparseable_fixture_names_provider_and_file(root: Path) -> None:
    (root / "provider" / "lookup.json").write_text("{not json")
    with pytest.raises(FixtureSchemaError) as info:
        await send(make(root), LOOKUP)
    assert info.value.field == "provider/lookup.json"


@pytest.mark.parametrize("name", ["../x", "a/b", "", ".hidden", "a\\b"])
def test_unsafe_endpoint_names_are_rejected_at_construction(
    root: Path, name: str
) -> None:
    with pytest.raises(ValueError, match="invalid endpoint name"):
        FixtureTransport("provider", {name: SEARCH}, fixtures_root=root)


def test_unsafe_provider_is_rejected_at_construction(root: Path) -> None:
    with pytest.raises(ValueError, match="invalid provider name"):
        FixtureTransport("../provider", ENDPOINTS, fixtures_root=root)


def test_one_endpoint_under_two_names_is_rejected(root: Path) -> None:
    with pytest.raises(ValueError, match="declared as both"):
        FixtureTransport("provider", {"a": SEARCH, "b": SEARCH}, fixtures_root=root)


def test_default_root_is_the_slice_fixtures_directory() -> None:
    t = FixtureTransport("provider", ENDPOINTS)
    assert t.fixtures_root == Path(transport_module.__file__).parent / "fixtures"


# Verifies: specs/lead-source-adapters/requirements.md#11.5
async def test_construction_and_send_open_no_socket(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("socket used")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    resp = await send(make(root), SEARCH)
    assert resp.body == PAYLOAD


# Verifies: specs/lead-source-adapters/requirements.md#11.5
def test_fixture_transport_holds_no_http_client_or_socket_attribute(
    root: Path,
) -> None:
    t = make(root)
    for value in vars(t).values():
        assert type(value).__module__.split(".")[0] not in {"httpx", "socket"}


# Verifies: specs/lead-source-adapters/requirements.md#11.5
def test_fixture_transport_body_references_no_network_module() -> None:
    tree = ast.parse(Path(transport_module.__file__).read_text())
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "FixtureTransport"
    )
    names = {n.id for n in ast.walk(cls) if isinstance(n, ast.Name)}
    assert not names & {"httpx", "socket", "urllib", "requests", "aiohttp"}
