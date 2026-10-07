"""Read-only endpoints and environment-only credentials (task 3.4)."""

import dataclasses
import pickle
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pytest

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
    resolve_credentials,
)
from leadforge.lead_ingestion.errors import MissingCredentialError, SourceError
from leadforge.lead_ingestion.models import DataMode

BUCKET = RateBucket(
    name="default",
    windows=(RateWindow(requests=60, per_seconds=60.0),),
    documented=True,
    doc_url="https://example.com/rate-limits",
)
SEARCH = Endpoint(method="GET", path="/v1/people/search", bucket="default")
LOOKUP = Endpoint(method="GET", path="/v1/people/{id}", bucket="default")


class Provider(BaseLeadSource):
    name: ClassVar[str] = "provider"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {"default": BUCKET}
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        "email": frozenset({"person.email"})
    }
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"search": SEARCH, "lookup": LOOKUP}
    required_env: ClassVar[tuple[str, ...]] = ("PROVIDER_API_KEY", "PROVIDER_SECRET")

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        return RawBatch(source_name=self.name, payload=None)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


def _attrs(**overrides: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "fetch_raw": Provider.fetch_raw,
        "normalize": Provider.normalize,
        "name": "variant",
        "capabilities": frozenset[Capability](),
        "rate_limit": {"default": BUCKET},
        "answerable_surfaces": {},
        "cost_class": CostClass.FREE,
        "charge_unit": ChargeUnit.PER_CALL,
        "yields_suppression": False,
        "target_vocabulary": {},
        "endpoints": {"search": SEARCH},
        "required_env": ("VARIANT_API_KEY",),
    }
    attrs.update(overrides)
    return attrs


def _build(**overrides: Any) -> BaseLeadSource:
    cls = type("Variant", (BaseLeadSource,), _attrs(**overrides))
    return cls(DataMode.SYNTHETIC)  # type: ignore[no-any-return]


# ------------------------------------------------- contract (integration) test


# Verifies: specs/lead-source-adapters/requirements.md#2.5
# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_adapter_declares_endpoints_and_resolves_credentials_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_API_KEY", "key-value")
    monkeypatch.setenv("PROVIDER_SECRET", "secret-value")
    source = Provider(DataMode.LIVE)

    assert set(source.endpoints.values()) == {SEARCH, LOOKUP}
    assert all(e.read_only is True for e in source.endpoints.values())
    assert resolve_credentials(source) == {
        "PROVIDER_API_KEY": "key-value",
        "PROVIDER_SECRET": "secret-value",
    }


# ----------------------------------------------------------------- Endpoint


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_endpoint_is_read_only_by_default_and_frozen() -> None:
    assert SEARCH.read_only is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        SEARCH.read_only = False  # type: ignore[assignment,misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        SEARCH.path = "/other"  # type: ignore[misc]


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_a_write_endpoint_cannot_be_constructed() -> None:
    # mypy (strict, warn_unused_ignores) rejects this line; the runtime does too.
    with pytest.raises(ValueError, match="read_only"):
        Endpoint(method="POST", path="/send", bucket="default", read_only=False)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize("value", [0, 1, None, "True", "yes"])
def test_only_the_literal_true_is_accepted_for_read_only(value: object) -> None:
    with pytest.raises(ValueError, match="read_only"):
        Endpoint(method="GET", path="/x", bucket="b", read_only=value)  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#11.2
@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE", "get", "", None])
def test_only_get_and_post_methods_are_accepted(method: object) -> None:
    with pytest.raises(ValueError, match="method"):
        Endpoint(method=method, path="/x", bucket="b")  # type: ignore[arg-type]


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize("field", ["path", "bucket"])
@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_endpoint_path_and_bucket_must_be_non_blank_str(
    field: str, value: object
) -> None:
    kwargs: dict[str, Any] = {"method": "GET", "path": "/x", "bucket": "b"}
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        Endpoint(**kwargs)


# ------------------------------------------------------- endpoints declaration


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_endpoints_declaration_is_required() -> None:
    attrs = _attrs()
    del attrs["endpoints"]
    cls = type("NoEndpoints", (BaseLeadSource,), attrs)
    with pytest.raises(TypeError, match=r"NoEndpoints.*endpoints"):
        cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_an_adapter_with_no_endpoints_is_valid() -> None:
    assert dict(_build(endpoints={}).endpoints) == {}


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_endpoints_are_frozen_when_the_subclass_is_defined() -> None:
    declared: Any = Provider.endpoints
    with pytest.raises(TypeError):
        declared["send"] = Endpoint(method="POST", path="/send", bucket="default")
    with pytest.raises(TypeError):
        del declared["search"]

    source_dict = {"search": SEARCH}
    source = _build(endpoints=source_dict)
    source_dict["injected"] = LOOKUP
    assert "injected" not in source.endpoints


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    "bad",
    [
        [SEARCH],
        {"": SEARCH},
        {"  ": SEARCH},
        {5: SEARCH},
        {"search": {"method": "GET", "path": "/x", "bucket": "default"}},
        {"search": None},
    ],
)
def test_malformed_endpoints_are_rejected_at_construction(bad: object) -> None:
    with pytest.raises(TypeError, match="endpoints"):
        _build(endpoints=bad)


# Verifies: specs/lead-source-adapters/requirements.md#11.1
def test_an_endpoint_on_an_undeclared_rate_bucket_is_rejected() -> None:
    ghost = Endpoint(method="GET", path="/x", bucket="ghost")
    with pytest.raises(TypeError, match="ghost"):
        _build(endpoints={"x": ghost})


# ------------------------------------------------------------- required_env


# Verifies: specs/lead-source-adapters/requirements.md#10.4
def test_required_env_declaration_is_required() -> None:
    attrs = _attrs()
    del attrs["required_env"]
    cls = type("NoEnv", (BaseLeadSource,), attrs)
    with pytest.raises(TypeError, match=r"NoEnv.*required_env"):
        cls(DataMode.SYNTHETIC)


# Verifies: specs/lead-source-adapters/requirements.md#2.5
@pytest.mark.parametrize(
    "bad",
    [
        "PROVIDER_API_KEY",
        ["PROVIDER_API_KEY"],
        ("",),
        ("  ",),
        (5,),
        ("A", "A"),
        ("HAS SPACE",),
        ("HAS=EQUALS",),
    ],
)
def test_malformed_required_env_is_rejected_at_construction(bad: object) -> None:
    with pytest.raises(TypeError, match="required_env"):
        _build(required_env=bad)


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_a_keyless_provider_declares_no_credentials() -> None:
    source = _build(required_env=())
    assert resolve_credentials(source, environ={}) == {}


# ------------------------------------------------------ credential resolver


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_resolver_reads_the_process_environment_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Provider(DataMode.LIVE)
    monkeypatch.setenv("PROVIDER_API_KEY", "first")
    monkeypatch.setenv("PROVIDER_SECRET", "s")
    assert resolve_credentials(source)["PROVIDER_API_KEY"] == "first"
    monkeypatch.setenv("PROVIDER_API_KEY", "second")
    assert resolve_credentials(source)["PROVIDER_API_KEY"] == "second"


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_a_missing_variable_raises_naming_provider_and_variable_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROVIDER_API_KEY", "super-secret-value")
    monkeypatch.delenv("PROVIDER_SECRET", raising=False)
    with pytest.raises(MissingCredentialError) as caught:
        resolve_credentials(Provider(DataMode.LIVE))
    error = caught.value
    assert isinstance(error, SourceError)
    assert error.source_name == "provider"
    assert error.missing == ("PROVIDER_SECRET",)
    assert "PROVIDER_SECRET" in str(error)
    assert "super-secret-value" not in str(error)


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_every_missing_variable_is_reported_together() -> None:
    with pytest.raises(MissingCredentialError) as caught:
        resolve_credentials(Provider(DataMode.LIVE), environ={})
    assert caught.value.missing == ("PROVIDER_API_KEY", "PROVIDER_SECRET")


# Verifies: specs/lead-source-adapters/requirements.md#2.5
@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_value_counts_as_missing(blank: str) -> None:
    env = {"PROVIDER_API_KEY": blank, "PROVIDER_SECRET": "s"}
    with pytest.raises(MissingCredentialError) as caught:
        resolve_credentials(Provider(DataMode.LIVE), environ=env)
    assert caught.value.missing == ("PROVIDER_API_KEY",)


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_resolver_returns_only_declared_names_and_a_read_only_view() -> None:
    env = {"PROVIDER_API_KEY": "k", "PROVIDER_SECRET": "s", "UNRELATED": "x"}
    resolved: Any = resolve_credentials(Provider(DataMode.LIVE), environ=env)
    assert set(resolved) == {"PROVIDER_API_KEY", "PROVIDER_SECRET"}
    with pytest.raises(TypeError):
        resolved["PROVIDER_API_KEY"] = "tampered"
    assert env["PROVIDER_API_KEY"] == "k"


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_there_is_no_file_or_literal_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in Provider.required_env:
        monkeypatch.delenv(name, raising=False)
    # A .env and config files carrying the values must not be consulted here
    # (loading .env into the process environment is a separate, later step).
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / ".env").write_text("PROVIDER_API_KEY=file-value\n")
    (config / "providers.yaml").write_text("provider:\n  PROVIDER_API_KEY: file\n")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(MissingCredentialError):
        resolve_credentials(Provider(DataMode.LIVE))


# Verifies: specs/lead-source-adapters/requirements.md#2.5
def test_resolver_source_has_no_file_reads_or_secret_defaults() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(resolve_credentials).lstrip())
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute | ast.Name)
    }
    assert not called & {"open", "read_text", "getenv", "load_dotenv"}


# Verifies: specs/lead-source-adapters/requirements.md#10.5
def test_missing_credential_error_pickles_and_carries_no_values() -> None:
    error = MissingCredentialError("provider", missing=("A", "B"))
    clone = pickle.loads(pickle.dumps(error))
    assert isinstance(clone, MissingCredentialError)
    assert (clone.source_name, clone.missing) == ("provider", ("A", "B"))


# Verifies: specs/lead-source-adapters/requirements.md#11.1
@pytest.mark.parametrize(
    "path",
    [
        "v1/people",
        "https://evil.example/v1",
        "//evil.example/v1",
        "/v1//people",
        "/v1/../admin",
        "/v1/./x",
        "/v1/people?send=1",
        "/v1/people#x",
        "/v1\\people",
        "/v1/pe ople",
    ],
)
def test_endpoint_path_must_stay_on_the_provider_host(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        Endpoint(method="GET", path=path, bucket="b")


# Verifies: specs/lead-source-adapters/requirements.md#11.2
def test_an_endpoint_subclass_cannot_be_declared() -> None:
    @dataclasses.dataclass(frozen=True)
    class Sneaky(Endpoint):
        def __post_init__(self) -> None:  # skips every check
            pass

    sneaky = Sneaky(method="GET", path="/x", bucket="default", read_only=False)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="endpoints"):
        _build(endpoints={"x": sneaky})
