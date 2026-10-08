"""Classifier selection and model settings (requirements 5.5, 5.6, 5.7)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel

from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.tests.socket_guard import SocketGuard, guard_for_mode
from leadforge.outreach.config import load_outreach_config
from leadforge.outreach.errors import UsageClassifierUnavailableError
from leadforge.outreach.llm import build_chat_model, llm_settings
from leadforge.outreach.usage.classify import LlmClassifier, OfflineClassifier
from leadforge.outreach.usage.flow import classifier_for

CONFIG = Path(__file__).resolve().parents[4] / "config"
CFG = load_outreach_config(CONFIG / "outreach.yaml")
KEY = f"{CFG.llm.provider.upper()}_API_KEY"


@pytest.fixture
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[SocketGuard]:
    guard = guard_for_mode(DataMode.SYNTHETIC)
    guard.install(monkeypatch)
    yield guard
    guard.assert_clean()


# Verifies: specs/user-recognition/requirements.md#5.5
def test_the_classifier_defaults_to_low_effort() -> None:
    assert CFG.usage.classifier.effort == "low"


# Verifies: specs/user-recognition/requirements.md#5.5
def test_the_chat_model_gets_provider_model_and_effort(
    offline_guard: SocketGuard,
) -> None:
    environ = {KEY: "test-key"}
    settings = llm_settings(environ, CFG.llm)
    assert settings is not None
    chat = build_chat_model(settings, environ, effort="low")
    assert isinstance(chat, BaseChatModel)
    assert chat.model == CFG.llm.model  # type: ignore[attr-defined]
    assert chat.reasoning_effort == "low"  # type: ignore[attr-defined]
    assert settings.provider == CFG.llm.provider


# Verifies: specs/user-recognition/requirements.md#5.5
def test_the_existing_compilers_set_no_effort(offline_guard: SocketGuard) -> None:
    environ = {KEY: "test-key"}
    settings = llm_settings(environ, CFG.llm)
    assert settings is not None
    chat = build_chat_model(settings, environ)
    assert chat.reasoning_effort is None  # type: ignore[attr-defined]


# Verifies: specs/user-recognition/requirements.md#5.5
def test_an_unsupported_provider_fails_fast_on_effort() -> None:
    settings = llm_settings(
        {"LEADFORGE_LLM_PROVIDER": "other", "OTHER_API_KEY": "k"}, CFG.llm
    )
    assert settings is not None
    with pytest.raises(ValueError, match="reasoning effort"):
        build_chat_model(settings, {"OTHER_API_KEY": "k"}, effort="low")


# Verifies: specs/user-recognition/requirements.md#5.6
def test_the_synthetic_flow_is_offline_even_with_a_key(
    offline_guard: SocketGuard,
) -> None:
    got = classifier_for(
        synthetic=True, usage=CFG.usage, llm=CFG.llm, environ={KEY: "test-key"}
    )
    assert isinstance(got, OfflineClassifier)


# Verifies: specs/user-recognition/requirements.md#5.7
def test_a_live_flow_with_no_key_raises_before_any_provider_call(
    offline_guard: SocketGuard,
) -> None:
    with pytest.raises(UsageClassifierUnavailableError):
        classifier_for(synthetic=False, usage=CFG.usage, llm=CFG.llm, environ={})


# Verifies: specs/user-recognition/requirements.md#5.5
def test_a_live_flow_with_a_key_builds_the_llm_classifier(
    offline_guard: SocketGuard,
) -> None:
    got = classifier_for(
        synthetic=False, usage=CFG.usage, llm=CFG.llm, environ={KEY: "test-key"}
    )
    assert isinstance(got, LlmClassifier)
