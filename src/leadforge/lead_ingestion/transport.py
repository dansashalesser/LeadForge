"""Transport port and its REST implementation (task 4.1).

Every provider call travels through ``Transport.send``. Nothing above the adapter
contract sees an ``httpx`` type: requests are described by an ``Endpoint`` plus plain
mappings, and answers come back as a ``TransportResponse``. The transport only ever
reaches paths in the owning adapter's declared endpoint map (11.1).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    SourceTimedOut,
    SourceTransient,
    UndeclaredEndpointError,
)

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_S",
    "DEFAULT_READ_TIMEOUT_S",
    "RestTransport",
    "Transport",
    "TransportResponse",
]

DEFAULT_CONNECT_TIMEOUT_S = 5.0
DEFAULT_READ_TIMEOUT_S = 30.0

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


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
        self._provider = provider
        self._declared = frozenset(endpoints.values())
        self._timeout = httpx.Timeout(
            connect=connect_timeout_s,
            read=read_timeout_s,
            write=read_timeout_s,
            pool=connect_timeout_s,
        )
        self._client = httpx.AsyncClient(base_url=base_url)

    async def aclose(self) -> None:
        await self._client.aclose()

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
            raise SourceTimedOut(self._provider, f"{endpoint.path}: {exc!r}") from exc
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


def _fill_path(
    template: str, params: Mapping[str, object]
) -> tuple[str, dict[str, str]]:
    """Substitute ``{name}`` from params; consumed names are not sent as query."""
    used: set[str] = set()

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in params:
            raise ValueError(f"missing path parameter {name!r} for {template}")
        used.add(name)
        return quote(str(params[name]), safe="")

    path = _PLACEHOLDER.sub(sub, template)
    return path, {k: _query_value(v) for k, v in params.items() if k not in used}


def _query_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
