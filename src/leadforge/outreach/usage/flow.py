"""Which usage classifier a run gets (requirements 5.5, 5.6, 5.7).

The synthetic flow is offline: no network, no model. A live flow needs a model key and
raises ``UsageClassifierUnavailableError`` without one, before any provider call; it
never falls back to the offline classifier."""

from collections.abc import Mapping

from leadforge.outreach.config import LlmConfig, UsageConfig
from leadforge.outreach.errors import UsageClassifierUnavailableError
from leadforge.outreach.llm import StructuredModel, build_chat_model, llm_settings
from leadforge.outreach.prompts import load_prompt
from leadforge.outreach.usage.classify import (
    Judgement,
    LlmClassifier,
    OfflineClassifier,
)

__all__ = ["classifier_for"]


def classifier_for(
    *,
    synthetic: bool,
    usage: UsageConfig,
    llm: LlmConfig,
    environ: Mapping[str, str],
) -> OfflineClassifier | LlmClassifier:
    if synthetic:
        return OfflineClassifier(usage.cues)
    settings = llm_settings(environ, llm)
    if settings is None:
        raise UsageClassifierUnavailableError
    chat = build_chat_model(settings, environ, effort=usage.classifier.effort)
    return LlmClassifier(
        StructuredModel(chat, Judgement),
        load_prompt(usage.classifier.prompt),
        model_name=settings.model,
        retries=llm.compile_retries,
    )
