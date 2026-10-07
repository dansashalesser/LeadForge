"""Authentication strategy port and OAuth2 token cache (task 4.4)."""

import asyncio
import base64
from collections.abc import Mapping

import pytest

from leadforge.lead_ingestion.auth import (
    AuthenticatedRequest,
    AuthStrategy,
    BearerAuth,
    HeaderKeyAuth,
    OAuth2ClientCredentialsAuth,
    QueryParamKeyAuth,
    Token,
    TokenCache,
)
from leadforge.lead_ingestion.base_source import Endpoint
from leadforge.lead_ingestion.errors import (
    MissingCredentialError,
    SourceTransient,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.transport import TransportResponse

SECRET = "s3cr3t-value-XYZ"
ENV = {"PROV_KEY": SECRET, "PROV_ID": "cid", "PROV_SECRET": "csecret"}
TOKEN_EP = Endpoint(method="POST", path="/oauth/token", bucket="default")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- static schemes ---------------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#2.5
async def test_header_key_sends_key_in_named_header_without_prefix() -> None:
    auth = HeaderKeyAuth("p", "PROV_KEY", header="X-Api-Key", environ=ENV)
    out = await auth.apply(headers={"Accept": "a"}, params={"q": 1})
    assert out.headers == {"Accept": "a", "X-Api-Key": SECRET}
    assert out.params == {"q": 1}


# Verifies: specs/lead-source-adapters/requirements.md#2.5
async def test_bare_authorization_header_has_no_bearer_prefix() -> None:
    auth = HeaderKeyAuth("p", "PROV_KEY", header="Authorization", environ=ENV)
    out = await auth.apply(headers={}, params={})
    assert out.headers == {"Authorization": SECRET}


# Verifies: specs/lead-source-adapters/requirements.md#2.5
async def test_bearer_prefixes_token() -> None:
    out = await BearerAuth("p", "PROV_KEY", environ=ENV).apply(headers={}, params={})
    assert out.headers == {"Authorization": f"Bearer {SECRET}"}


# Verifies: specs/lead-source-adapters/requirements.md#2.5
async def test_query_param_key_adds_param_and_leaves_headers() -> None:
    auth = QueryParamKeyAuth("p", "PROV_KEY", param="api_key", environ=ENV)
    out = await auth.apply(headers={"A": "b"}, params={"q": 1})
    assert out.params == {"q": 1, "api_key": SECRET}
    assert out.headers == {"A": "b"}


async def test_auth_overrides_caller_header_case_insensitively_and_copies_inputs() -> (
    None
):
    headers = {"authorization": "old"}
    params: dict[str, object] = {"api_key": "old"}
    h = await BearerAuth("p", "PROV_KEY", environ=ENV).apply(
        headers=headers, params=params
    )
    q = await QueryParamKeyAuth("p", "PROV_KEY", param="api_key", environ=ENV).apply(
        headers=headers, params=params
    )
    assert h.headers == {"Authorization": f"Bearer {SECRET}"}
    assert q.params == {"api_key": SECRET}
    assert headers == {"authorization": "old"}
    assert params == {"api_key": "old"}


# Verifies: specs/lead-source-adapters/requirements.md#2.5
@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_env_is_missing_credential_naming_variable_only(
    value: str | None,
) -> None:
    env = {} if value is None else {"PROV_KEY": value}
    with pytest.raises(MissingCredentialError) as exc:
        BearerAuth("p", "PROV_KEY", environ=env)
    assert exc.value.missing == ("PROV_KEY",)
    assert exc.value.source_name == "p"


def test_credentials_come_from_process_environment_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROV_KEY", SECRET)
    assert BearerAuth("p", "PROV_KEY") is not None
    monkeypatch.delenv("PROV_KEY")
    with pytest.raises(MissingCredentialError):
        BearerAuth("p", "PROV_KEY")


@pytest.mark.parametrize("bad", ["", "A=B", "has space", "x\0y"])
def test_invalid_env_variable_name_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="environment variable name"):
        BearerAuth("p", bad, environ=ENV)


@pytest.mark.parametrize("value", ["a\r\nX-Evil: 1", "a\nb", "a\0b"])
def test_credential_with_control_characters_rejected_without_echoing_it(
    value: str,
) -> None:
    with pytest.raises(ValueError, match="PROV_KEY") as exc:
        BearerAuth("p", "PROV_KEY", environ={"PROV_KEY": value})
    assert value not in str(exc.value)


def test_blank_header_or_param_name_rejected() -> None:
    with pytest.raises(ValueError, match="header"):
        HeaderKeyAuth("p", "PROV_KEY", header=" ", environ=ENV)
    with pytest.raises(ValueError, match="param"):
        QueryParamKeyAuth("p", "PROV_KEY", param="", environ=ENV)


def test_repr_never_contains_secret() -> None:
    strategies: list[AuthStrategy] = [
        HeaderKeyAuth("p", "PROV_KEY", header="X", environ=ENV),
        BearerAuth("p", "PROV_KEY", environ=ENV),
        QueryParamKeyAuth("p", "PROV_KEY", param="k", environ=ENV),
        OAuth2ClientCredentialsAuth(
            "p",
            client_id_env="PROV_ID",
            client_secret_env="PROV_SECRET",
            token_endpoint=TOKEN_EP,
            transport=ScriptedTransport([]),
            environ=ENV,
        ),
    ]
    for s in strategies:
        text = repr(s) + str(s)
        assert SECRET not in text
        assert "csecret" not in text
        assert "PROV_" in text


def test_strategies_satisfy_port() -> None:
    s: AuthStrategy = BearerAuth("p", "PROV_KEY", environ=ENV)
    assert isinstance(AuthenticatedRequest({}, {}), AuthenticatedRequest)
    assert s is not None


# --- token cache ------------------------------------------------------------------


class Fetcher:
    def __init__(self, clock: Clock, expires_in: float = 3600.0) -> None:
        self.calls = 0
        self.expires_in = expires_in
        self.gate: asyncio.Event | None = None
        self.fail: BaseException | None = None

    async def __call__(self) -> Token:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail is not None:
            raise self.fail
        return Token(value=f"tok{self.calls}", expires_in_s=self.expires_in)


# Verifies: specs/lead-source-adapters/requirements.md#18.7
async def test_cache_reuses_one_token_across_many_calls() -> None:
    clock = Clock()
    fetch = Fetcher(clock)
    cache = TokenCache(fetch, clock=clock)
    tokens = [await cache.get() for _ in range(50)]
    assert set(tokens) == {"tok1"}
    assert fetch.calls == 1


# Verifies: specs/lead-source-adapters/requirements.md#18.3
async def test_cache_refreshes_before_the_validity_window_expires() -> None:
    clock = Clock()
    fetch = Fetcher(clock, expires_in=3600)
    cache = TokenCache(fetch, refresh_margin_s=60, clock=clock)
    assert await cache.get() == "tok1"
    clock.now += 3600 - 61
    assert await cache.get() == "tok1"
    clock.now += 2  # inside the margin, token still valid at the provider
    assert await cache.get() == "tok2"
    assert fetch.calls == 2


async def test_margin_is_clamped_for_short_lived_tokens() -> None:
    clock = Clock()
    fetch = Fetcher(clock, expires_in=10)
    cache = TokenCache(fetch, refresh_margin_s=60, clock=clock)
    await cache.get()
    await cache.get()
    assert fetch.calls == 1  # not refetched on every call
    clock.now += 6  # past half the lifetime
    await cache.get()
    assert fetch.calls == 2


async def test_concurrent_callers_share_one_in_flight_refresh() -> None:
    clock = Clock()
    fetch = Fetcher(clock)
    fetch.gate = asyncio.Event()
    cache = TokenCache(fetch, clock=clock)
    tasks = [asyncio.create_task(cache.get()) for _ in range(20)]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    fetch.gate.set()
    assert set(await asyncio.gather(*tasks)) == {"tok1"}
    assert fetch.calls == 1


async def test_failed_fetch_is_not_cached_and_next_call_retries() -> None:
    clock = Clock()
    fetch = Fetcher(clock)
    fetch.fail = SourceTransient("p")
    cache = TokenCache(fetch, clock=clock)
    with pytest.raises(SourceTransient):
        await cache.get()
    fetch.fail = None
    assert await cache.get() == "tok2"


async def test_cancelled_refresh_releases_the_lock() -> None:
    clock = Clock()
    fetch = Fetcher(clock)
    fetch.gate = asyncio.Event()
    cache = TokenCache(fetch, clock=clock)
    t = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    fetch.gate.set()
    assert await asyncio.wait_for(cache.get(), 1) == "tok2"


async def test_invalidate_drops_only_the_rejected_token() -> None:
    clock = Clock()
    fetch = Fetcher(clock)
    cache = TokenCache(fetch, clock=clock)
    assert await cache.get() == "tok1"
    cache.invalidate("some-other-token")  # stale rejection of an older token
    assert await cache.get() == "tok1"
    cache.invalidate("tok1")
    assert await cache.get() == "tok2"
    cache.invalidate()  # unconditional
    assert await cache.get() == "tok3"


@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf")])
async def test_non_positive_or_non_finite_lifetime_rejected(bad: float) -> None:
    clock = Clock()
    cache = TokenCache(Fetcher(clock, expires_in=bad), clock=clock)
    with pytest.raises(ValueError, match="expires_in"):
        await cache.get()


def test_token_and_cache_repr_hide_value() -> None:
    t = Token(value=SECRET, expires_in_s=5)
    assert SECRET not in repr(t)
    clock = Clock()
    assert "tok" not in repr(TokenCache(Fetcher(clock), clock=clock))


def test_negative_margin_rejected() -> None:
    clock = Clock()
    with pytest.raises(ValueError, match="margin"):
        TokenCache(Fetcher(clock), refresh_margin_s=-1, clock=clock)


async def test_window_is_measured_from_the_request_not_the_response() -> None:
    clock = Clock()

    async def slow_fetch() -> Token:
        clock.now += 50  # a slow token endpoint
        return Token(value="t", expires_in_s=100)

    cache = TokenCache(slow_fetch, refresh_margin_s=0, clock=clock)
    await cache.get()
    clock.now += 49  # 99 s after the request: still inside the window
    assert cache._fresh() == "t"
    clock.now += 2
    assert cache._fresh() is None


# --- OAuth2 client credentials ----------------------------------------------------


class ScriptedTransport:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.calls.append(
            {"endpoint": endpoint, "params": params, "body": json_body, "h": headers}
        )
        return self.responses.pop(0)


def _ok(token: str = "T1", expires_in: object = 3600) -> TransportResponse:
    return TransportResponse(
        200, {}, {"access_token": token, "expires_in": expires_in, "token_type": "x"}
    )


def _oauth(
    transport: ScriptedTransport, clock: Clock | None = None
) -> OAuth2ClientCredentialsAuth:
    return OAuth2ClientCredentialsAuth(
        "p",
        client_id_env="PROV_ID",
        client_secret_env="PROV_SECRET",
        token_endpoint=TOKEN_EP,
        transport=transport,
        environ=ENV,
        clock=clock or Clock(),
    )


# Verifies: specs/lead-source-adapters/requirements.md#18.3
async def test_oauth_fetches_token_with_basic_auth_and_applies_bearer() -> None:
    transport = ScriptedTransport([_ok()])
    out = await _oauth(transport).apply(headers={"Accept": "a"}, params={})
    assert out.headers == {"Accept": "a", "Authorization": "Bearer T1"}
    (call,) = transport.calls
    assert call["endpoint"] == TOKEN_EP
    assert call["params"] == {"grant_type": "client_credentials"}
    expected = base64.b64encode(b"cid:csecret").decode()
    assert call["h"] == {
        "Authorization": f"Basic {expected}",
        "Accept": "application/json",
    }


# Verifies: specs/lead-source-adapters/requirements.md#18.7
async def test_oauth_reuses_one_token_across_many_calls_and_refreshes_early() -> None:
    clock = Clock()
    transport = ScriptedTransport([_ok("T1", 300), _ok("T2", 300)])
    auth = _oauth(transport, clock)
    for _ in range(10):
        out = await auth.apply(headers={}, params={})
        assert out.headers["Authorization"] == "Bearer T1"
    assert len(transport.calls) == 1
    clock.now += 300 - 30  # inside the default 60 s margin, before expiry
    out = await auth.apply(headers={}, params={})
    assert out.headers["Authorization"] == "Bearer T2"
    assert len(transport.calls) == 2


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_oauth_missing_credentials_name_every_missing_variable() -> None:
    with pytest.raises(MissingCredentialError) as exc:
        OAuth2ClientCredentialsAuth(
            "p",
            client_id_env="PROV_ID",
            client_secret_env="PROV_SECRET",
            token_endpoint=TOKEN_EP,
            transport=ScriptedTransport([]),
            environ={},
        )
    assert exc.value.missing == ("PROV_ID", "PROV_SECRET")


def test_oauth_rejects_colon_in_client_id() -> None:
    with pytest.raises(ValueError, match="PROV_ID"):
        OAuth2ClientCredentialsAuth(
            "p",
            client_id_env="PROV_ID",
            client_secret_env="PROV_SECRET",
            token_endpoint=TOKEN_EP,
            transport=ScriptedTransport([]),
            environ={**ENV, "PROV_ID": "a:b"},
        )


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_oauth_token_rejection_is_source_unauthorized(status: int) -> None:
    transport = ScriptedTransport([TransportResponse(status, {}, {"error": SECRET})])
    with pytest.raises(SourceUnauthorized) as exc:
        await _oauth(transport).apply(headers={}, params={})
    assert exc.value.endpoint == "/oauth/token"
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_oauth_server_trouble_is_transient(status: int) -> None:
    transport = ScriptedTransport([TransportResponse(status, {}, None)])
    with pytest.raises(SourceTransient):
        await _oauth(transport).apply(headers={}, params={})


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"access_token": "", "expires_in": 5},
        {"access_token": 7, "expires_in": 5},
        {"access_token": "t"},
        {"access_token": "t", "expires_in": "soon"},
        {"access_token": "t", "expires_in": True},
        {"access_token": "t", "expires_in": 0},
        {"access_token": "a\r\nb", "expires_in": 5},
    ],
)
async def test_oauth_malformed_token_response_is_unauthorized(body: object) -> None:
    transport = ScriptedTransport([TransportResponse(200, {}, body)])
    with pytest.raises(SourceUnauthorized) as exc:
        await _oauth(transport).apply(headers={}, params={})
    assert "a\r\nb" not in str(exc.value)


async def test_oauth_failure_is_not_cached() -> None:
    transport = ScriptedTransport([TransportResponse(401, {}, None), _ok()])
    auth = _oauth(transport)
    with pytest.raises(SourceUnauthorized):
        await auth.apply(headers={}, params={})
    out = await auth.apply(headers={}, params={})
    assert out.headers["Authorization"] == "Bearer T1"
