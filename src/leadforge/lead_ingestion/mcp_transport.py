"""MCP transport seam with interactive-auth fallback (task 4.3).

``McpTransport`` satisfies the same ``Transport`` port as the REST and fixture
transports, so an MCP-backed source is indistinguishable above the adapter (20.1,
20.2). The MCP client itself is injected as a minimal ``McpSession``; this module
imports no MCP SDK type, and the SDK stays an optional dependency of whichever
adapter chooses MCP. When the server demands an interactive browser authorization,
the transport switches, for good, to the REST or synthetic transport it was given and
logs why (20.5): a headless run must never wait on a browser.
"""

from collections.abc import Mapping
from typing import Literal, Protocol

import structlog

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    SourceTimedOut,
    SourceTransient,
    UndeclaredEndpointError,
)
from leadforge.lead_ingestion.transport import Transport, TransportResponse

__all__ = ["McpInteractiveAuthError", "McpSession", "McpTransport"]

_log = structlog.get_logger(__name__)


class McpInteractiveAuthError(Exception):
    """Raised by an ``McpSession`` when the server needs a browser authorization."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class McpSession(Protocol):
    """The one MCP operation the transport needs: call a named tool, get parsed JSON."""

    async def call_tool(self, name: str, arguments: Mapping[str, object]) -> object: ...


class McpTransport:
    """``Transport`` over an MCP tool session. Tool name = the endpoint-map key."""

    def __init__(
        self,
        provider: str,
        endpoints: Mapping[str, Endpoint],
        session: McpSession,
        *,
        fallback: Transport,
        fallback_kind: Literal["rest", "synthetic"],
    ) -> None:
        if fallback_kind not in ("rest", "synthetic"):
            raise ValueError("fallback_kind must be 'rest' or 'synthetic'")
        self._provider = provider
        self._names = {endpoint: name for name, endpoint in endpoints.items()}
        self._session = session
        self._fallback = fallback
        self._fallback_kind = fallback_kind
        self._fell_back = False

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
        if not self._fell_back:
            # Headers carry REST credentials and are not forwarded; MCP auth belongs
            # to the session. The body wins over params on a name clash.
            arguments = {**(params or {}), **(json_body or {})}
            try:
                body = await self._session.call_tool(name, arguments)
            except McpInteractiveAuthError as exc:
                self._fell_back = True
                _log.warning(
                    "mcp_interactive_auth_fallback",
                    provider=self._provider,
                    fallback=self._fallback_kind,
                    reason=exc.reason,
                )
            except TimeoutError as exc:
                raise SourceTimedOut(self._provider, f"{name}: {exc!r}") from exc
            except OSError as exc:
                raise SourceTransient(self._provider) from exc
            else:
                return TransportResponse(status=200, headers={}, body=body)
        return await self._fallback.send(
            endpoint, params=params, json_body=json_body, headers=headers
        )
