"""Transport port, its REST implementation (4.1) and the fixture transport (4.2).

Every provider call travels through ``Transport.send``. Nothing above the adapter
contract sees an ``httpx`` type: requests are described by an ``Endpoint`` plus plain
mappings, and answers come back as a ``TransportResponse``. The transport only ever
reaches paths in the owning adapter's declared endpoint map (11.1).
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    FixtureSchemaError,
    SendCapableEndpointError,
    SourceTimedOut,
    SourceTransient,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.send_prohibition import assert_no_send_capable_endpoints

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_READ_TIMEOUT_S",
    "FixtureTransport",
    "RestTransport",
    "Transport",
    "TransportResponse",
]

DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_READ_TIMEOUT_S = 30.0

_METHOD_OVERRIDE_HEADERS = frozenset(
    {"x-http-method-override", "x-http-method", "x-method-override"}
)
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_FILE_STEM = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
_DEFAULT_FIXTURES_ROOT = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class TransportResponse:
    """Transport-neutral answer. ``body`` is parsed JSON, or None if not JSON."""

    status: int
    headers: Mapping[str, str]  # keys lower-cased
    body: object | None


class Transport(Protocol):
    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse: ...


class RestTransport:
    """``Transport`` over ``httpx.AsyncClient`` with explicit timeouts on every call."""

    def __init__(
        self,
        provider: str,
        base_url: str,
        endpoints: Mapping[str, Endpoint],
        *,
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        read_timeout_s: float = DEFAULT_READ_TIMEOUT_S,
    ) -> None:
        if connect_timeout_s <= 0 or read_timeout_s <= 0:
            raise ValueError("connect and read timeout must be positive seconds")
        assert_no_send_capable_endpoints(provider, endpoints)
        self._provider = provider
        self._declared = frozenset(endpoints.values())
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=read_timeout_s,
            pool=connect_timeout_s,
        )
        self._client = httpx.AsyncClient(base_url=base_url, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _refuse_write_shapes(
        self,
        endpoint: Endpoint,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> None:
        """Refuse a GET body or a method-override header: each could make a write."""
        if endpoint.method == "GET" and json_body is not None:
            reason = "a GET carries no body"
        elif any(h.lower() in _METHOD_OVERRIDE_HEADERS for h in headers):
            reason = "a method override header can turn a read into a write"
        else:
            return
        raise SendCapableEndpointError(
            self._provider, path=endpoint.path, reason=reason
        )

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        if endpoint not in self._declared:
            raise UndeclaredEndpointError(self._provider, path=endpoint.path)
        self._refuse_write_shapes(endpoint, json_body, headers)
        path, query = _fill_path(endpoint.path, params or {})
        try:
            response = await self._client.request(
                endpoint.method,
                path,
                params=query or None,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            # Name the error type only: a library message may carry a URL or data.
            raise SourceTimedOut(
                self._provider, f"{endpoint.path}: {type(exc).__name__}"
            ) from exc
        except httpx.TransportError as exc:
            raise SourceTransient(self._provider) from exc
        try:
            body: object | None = response.json()
        except ValueError:
            body = None
        return TransportResponse(
            status=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            body=body,
        )


class FixtureTransport:
    """``Transport`` that serves ``<root>/<provider>/<endpoint name>.json``.

    Synthetic mode is this substitution, not a branch in any adapter: it holds no
    socket, so a synthetic run cannot reach the network (4.1, 11.5), and the adapter's
    raw-schema validation and ``normalize()`` run unchanged on what it returns (5.2).
    The endpoint name is the key under which the adapter declares the endpoint.
    """

    def __init__(
        self,
        provider: str,
        endpoints: Mapping[str, Endpoint],
        *,
        fixtures_root: Path | None = None,
    ) -> None:
        if not _FILE_STEM.fullmatch(provider):
            raise ValueError(f"invalid provider name for fixtures: {provider!r}")
        assert_no_send_capable_endpoints(provider, endpoints)
        names: dict[Endpoint, str] = {}
        for name, endpoint in endpoints.items():
            if not _FILE_STEM.fullmatch(name):
                raise ValueError(f"invalid endpoint name for fixtures: {name!r}")
            if endpoint in names:
                raise ValueError(
                    f"endpoint {endpoint.path} declared as both "
                    f"{names[endpoint]!r} and {name!r}"
                )
            names[endpoint] = name
        self._provider = provider
        self._names = names
        self.fixtures_root = (
            _DEFAULT_FIXTURES_ROOT if fixtures_root is None else fixtures_root
        )

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        name = self._names.get(endpoint)
        if name is None:
            raise UndeclaredEndpointError(self._provider, path=endpoint.path)
        label = f"{self._provider}/{name}.json"
        try:
            body: object = json.loads(
                (self.fixtures_root / label).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise FixtureSchemaError(self._provider, field=label) from exc
        return TransportResponse(status=200, headers={}, body=body)


def _fill_path(
    template: str, params: Mapping[str, object]
) -> tuple[str, dict[str, str]]:
    """Substitute ``{name}`` from params; consumed names are not sent as query."""
    used: set[str] = set()

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise ValueError(f"missing path parameter {name!r} for {template}")
        value = str(params[name])
        if value in ("", ".", ".."):  # httpx collapses dot segments out of the path
            raise ValueError(f"invalid path parameter {name!r} for {template}")
        used.add(name)
        return quote(value, safe="")

    path = _PLACEHOLDER.sub(sub, template)
    return path, {k: _query_value(v) for k, v in params.items() if k not in used}


def _query_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
