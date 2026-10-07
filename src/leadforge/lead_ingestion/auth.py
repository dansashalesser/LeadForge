"""Authentication strategy port with a token cache (task 4.4).

An ``AuthStrategy`` turns a request's headers and parameters into authenticated ones.
The static schemes (custom key header, bearer token, request-parameter key) and the
OAuth2 client-credentials strategy sit behind the same port, so the abstraction is not
shaped around static headers alone (18.3, 18.7). Every credential is resolved from
declared environment variable names only (2.5), checked at construction so a missing
one fails before any request, and never appears in a ``repr`` or an exception message.
"""

import asyncio
import base64
import math
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.transport import Transport

__all__ = [
    "DEFAULT_REFRESH_MARGIN_S",
    "AuthStrategy",
    "AuthenticatedRequest",
    "BearerAuth",
    "HeaderKeyAuth",
    "OAuth2ClientCredentialsAuth",
    "QueryParamKeyAuth",
    "Token",
    "TokenCache",
]

DEFAULT_REFRESH_MARGIN_S = 60.0


@dataclass(frozen=True)
class AuthenticatedRequest:
    """Headers and parameters with credentials applied; the inputs are never mutated."""

    headers: Mapping[str, str]
    params: Mapping[str, object]


class AuthStrategy(Protocol):
    async def apply(
        self, *, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> AuthenticatedRequest: ...


def _is_env_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "=" not in name
        and not any(c.isspace() or c == "\0" for c in name)
    )


def _has_control(value: str) -> bool:
    return any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)


def _resolve(
    provider: str, names: tuple[str, ...], environ: Mapping[str, str] | None
) -> tuple[str, ...]:
    """Values for ``names`` from the environment; missing names reported together."""
    for name in names:
        if not _is_env_name(name):
            raise ValueError(f"invalid environment variable name: {name!r}")
    env = os.environ if environ is None else environ
    values = [env.get(name, "") for name in names]
    missing = tuple(n for n, v in zip(names, values, strict=True) if not v.strip())
    if missing:
        raise MissingCredentialError(provider, missing=missing)
    for name, value in zip(names, values, strict=True):
        if _has_control(value):
            # Header injection guard; the value itself is never echoed.
            raise ValueError(f"{name} contains control characters")
    return tuple(values)


def _with_header(headers: Mapping[str, str], name: str, value: str) -> dict[str, str]:
    lowered = name.lower()
    out = {k: v for k, v in headers.items() if k.lower() != lowered}
    out[name] = value
    return out


def _non_blank(kind: str, value: str) -> str:
    if not value.strip() or _has_control(value):
        raise ValueError(f"{kind} name must be a non-blank printable str")
    return value


class _StaticKeyAuth:
    """Shared construction and redacted repr for the env-var-keyed static schemes."""

    def __init__(
        self,
        provider: str,
        env_var: str,
        environ: Mapping[str, str] | None,
    ) -> None:
        self._provider = provider
        self._env_var = env_var
        (self._key,) = _resolve(provider, (env_var,), environ)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self._provider!r}, env={self._env_var})"
        )


class HeaderKeyAuth(_StaticKeyAuth):
    """The key, unprefixed, in a named header (``X-Api-Key`` or ``Authorization``)."""

    def __init__(
        self,
        provider: str,
        env_var: str,
        *,
        header: str,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._header = _non_blank("header", header)
        super().__init__(provider, env_var, environ)

    async def apply(
        self, *, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> AuthenticatedRequest:
        return AuthenticatedRequest(
            _with_header(headers, self._header, self._key), dict(params)
        )


class BearerAuth(_StaticKeyAuth):
    """``Authorization: Bearer <token>``."""

    def __init__(
        self,
        provider: str,
        env_var: str,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(provider, env_var, environ)

    async def apply(
        self, *, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> AuthenticatedRequest:
        return AuthenticatedRequest(
            _with_header(headers, "Authorization", f"Bearer {self._key}"), dict(params)
        )


class QueryParamKeyAuth(_StaticKeyAuth):
    """The key as a request parameter; prefer a header scheme where one is offered."""

    def __init__(
        self,
        provider: str,
        env_var: str,
        *,
        param: str,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._param = _non_blank("param", param)
        super().__init__(provider, env_var, environ)

    async def apply(
        self, *, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> AuthenticatedRequest:
        return AuthenticatedRequest(dict(headers), {**params, self._param: self._key})


@dataclass(frozen=True)
class Token:
    """An access token and its stated validity window in seconds."""

    value: str = field(repr=False)
    expires_in_s: float


class TokenCache:
    """Holds one token until shortly before it expires, then refreshes it.

    Refresh happens ``refresh_margin_s`` before expiry (clamped to half the lifetime,
    so a short-lived token is not refetched on every call), so a long run never takes
    a 401 from a token that lapsed in flight. Concurrent callers share one fetch. A
    failed fetch caches nothing. The clock is monotonic and injectable.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[Token]],
        *,
        refresh_margin_s: float = DEFAULT_REFRESH_MARGIN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (refresh_margin_s >= 0 and math.isfinite(refresh_margin_s)):
            raise ValueError("refresh margin must be a finite number of seconds >= 0")
        self._fetch = fetch
        self._margin = refresh_margin_s
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._refresh_at = 0.0

    def __repr__(self) -> str:
        return f"TokenCache(cached={self._token is not None})"

    def _fresh(self) -> str | None:
        if self._token is not None and self._clock() < self._refresh_at:
            return self._token
        return None

    async def get(self) -> str:
        token = self._fresh()
        if token is not None:
            return token
        async with self._lock:
            token = self._fresh()  # another caller may have refreshed while we waited
            if token is not None:
                return token
            requested = self._clock()  # the window starts no later than the request
            fetched = await self._fetch()
            lifetime = fetched.expires_in_s
            if not (math.isfinite(lifetime) and lifetime > 0):
                raise ValueError("token expires_in must be a positive finite number")
            self._token = fetched.value
            self._refresh_at = requested + lifetime - min(self._margin, lifetime / 2)
            return fetched.value

    def invalidate(self, rejected: str | None = None) -> None:
        """Drop the cached token, e.g. after a 401.

        With ``rejected``, drop only if that is still the cached token, so a late 401
        for an already-replaced token cannot evict its fresh successor.
        """
        if rejected is None or rejected == self._token:
            self._token = None


class OAuth2ClientCredentialsAuth:
    """OAuth2 client credentials: Basic-auth token request, then ``Bearer`` on calls.

    The token request travels through the same ``Transport`` port as data calls, so
    it is bound to the adapter's declared endpoint map like any other. The token is
    cached (``TokenCache``), so one token serves many paged calls.
    """

    def __init__(
        self,
        provider: str,
        *,
        client_id_env: str,
        client_secret_env: str,
        token_endpoint: Endpoint,
        transport: Transport,
        refresh_margin_s: float = DEFAULT_REFRESH_MARGIN_S,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._provider = provider
        self._env_names = (client_id_env, client_secret_env)
        client_id, client_secret = _resolve(provider, self._env_names, environ)
        if ":" in client_id:
            raise ValueError(f"{client_id_env} must not contain ':' (HTTP Basic)")
        raw = f"{client_id}:{client_secret}".encode()
        self._basic = base64.b64encode(raw).decode("ascii")
        self._endpoint = token_endpoint
        self._transport = transport
        self.cache = TokenCache(
            self._fetch_token, refresh_margin_s=refresh_margin_s, clock=clock
        )

    def __repr__(self) -> str:
        return (
            f"OAuth2ClientCredentialsAuth(provider={self._provider!r}, "
            f"env={', '.join(self._env_names)})"
        )

    async def apply(
        self, *, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> AuthenticatedRequest:
        token = await self.cache.get()
        return AuthenticatedRequest(
            _with_header(headers, "Authorization", f"Bearer {token}"), dict(params)
        )

    async def _fetch_token(self) -> Token:
        response = await self._transport.send(
            self._endpoint,
            params=MappingProxyType({"grant_type": "client_credentials"}),
            json_body=None,
            headers={
                "Authorization": f"Basic {self._basic}",
                "Accept": "application/json",
            },
        )
        if response.status in (400, 401, 403):
            # The body is not echoed: an error payload may repeat the credentials.
            raise SourceUnauthorized(
                self._provider,
                endpoint=self._endpoint.path,
                scope_cause=f"token request rejected (HTTP {response.status})",
            )
        if not 200 <= response.status < 300:
            raise SourceTransient(self._provider)
        return self._parse(response.body)

    def _parse(self, body: object) -> Token:
        def bad(why: str) -> SourceUnauthorized:
            return SourceUnauthorized(
                self._provider,
                endpoint=self._endpoint.path,
                scope_cause=f"malformed token response: {why}",
            )

        if not isinstance(body, dict):
            raise bad("not a JSON object")
        value = body.get("access_token")
        if not isinstance(value, str) or not value.strip() or _has_control(value):
            raise bad("access_token missing or invalid")
        expires_in = body.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, int | float)
            or not math.isfinite(expires_in)
            or expires_in <= 0
        ):
            raise bad("expires_in missing or not a positive number")
        return Token(value=value, expires_in_s=float(expires_in))
