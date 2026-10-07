"""Which model to call and how (requirements 14.2, 14.3).

The provider and model come from the environment (``LEADFORGE_LLM_PROVIDER``,
``LEADFORGE_LLM_MODEL``), falling back to ``config/outreach.yaml``; the key is read from
``<PROVIDER>_API_KEY``. With no key there is no model: callers then take the offline
path and say so. LangChain's model layer builds the chat model, so a different provider
is a different environment variable, not a code edit.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from leadforge.outreach.config import LlmConfig

__all__ = [
    "MODEL_ENV",
    "PROVIDER_ENV",
    "LlmSettings",
    "build_chat_model",
    "llm_settings",
]

PROVIDER_ENV = "LEADFORGE_LLM_PROVIDER"
MODEL_ENV = "LEADFORGE_LLM_MODEL"


@dataclass(frozen=True)
class LlmSettings:
    provider: str
    model: str
    key_env: str
    timeout_s: float


def llm_settings(environ: Mapping[str, str], config: LlmConfig) -> LlmSettings | None:
    """The model to use, or ``None`` when its key is not set (the offline path)."""
    provider = (environ.get(PROVIDER_ENV) or config.provider).strip().lower()
    model = (environ.get(MODEL_ENV) or config.model).strip()
    key_env = f"{provider.upper()}_API_KEY"
    if not environ.get(key_env, "").strip():
        return None
    return LlmSettings(provider, model, key_env, config.timeout_s)


def build_chat_model(
    settings: LlmSettings, environ: Mapping[str, str]
) -> BaseChatModel:
    """The LangChain chat model for ``settings``; opens no connection until called."""
    return init_chat_model(
        settings.model,
        model_provider=settings.provider,
        api_key=environ[settings.key_env],
        timeout=settings.timeout_s,
    )
