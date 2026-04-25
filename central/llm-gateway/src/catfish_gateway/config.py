"""Config loader -- parse models.yaml and provide access helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class UpstreamConfig(BaseModel):
    """Upstream LLM endpoint.

    Supports any LiteLLM-compatible provider. The `model` field must include
    the provider prefix, e.g. 'openai/qwen_v3_5_122b_a10b' or
    'gemini/gemini-2.5-pro'.
    """

    model: str
    api_base: str | None = None  # only set for OpenAI-compatible self-hosted endpoints
    api_key_env: str = "INTERNAL_LLM_KEY"

    # Parameters the gateway will FORCE on every request, regardless of what
    # the client sent. Use this for model-specific requirements like
    # "Gemini 3 must run at temperature=1.0" or "Claude must not set n>1".
    # Client-sent values for the same keys are silently replaced.
    param_overrides: dict[str, Any] = Field(default_factory=dict)

    # Per-upstream timeout in seconds. Some preview models (Gemini 3) can
    # be slow to cold-start; private fast models rarely need more than 60s.
    timeout: int = 60

    @property
    def api_key(self) -> str:
        """Read the API key from env at request time (not baked into config)."""
        key = os.environ.get(self.api_key_env)
        if not key:
            raise RuntimeError(
                f"env variable {self.api_key_env} is not set -- cannot call model '{self.model}'"
            )
        return key

    @property
    def is_available(self) -> bool:
        """True if the required API key is configured in the environment."""
        return bool(os.environ.get(self.api_key_env))


class ModelConfig(BaseModel):
    """A single model the gateway can route to."""

    name: str
    tier: str = "private"  # private | public
    display_name: str
    mode: str = "chat"  # chat | embedding
    default: bool = False

    upstream: UpstreamConfig

    context_window: int | None = None
    supports_tool_use: bool = False
    supports_streaming: bool = True
    supports_vision: bool = False
    recommended_for: list[str] = Field(default_factory=list)
    cost_tier: str = "free"  # free | paid


class Config(BaseModel):
    version: int = 1
    models: list[ModelConfig]

    def get_model(self, name: str) -> ModelConfig | None:
        for m in self.models:
            if m.name == name:
                return m
        return None

    def default_model(self) -> ModelConfig | None:
        for m in self.models:
            if m.default:
                return m
        return self.models[0] if self.models else None


def load_config(path: Path | None = None) -> Config:
    """Load models.yaml. Path can be overridden by CATFISH_CONFIG env."""
    if path is None:
        path = Path(os.environ.get("CATFISH_CONFIG", "config/models.yaml"))

    if not path.is_absolute():
        # try both cwd and the installed package dir
        candidates = [Path.cwd() / path, Path(__file__).parent.parent.parent / path]
        for c in candidates:
            if c.exists():
                path = c
                break

    if not Path(path).exists():
        raise FileNotFoundError(
            f"models config not found: {path} "
            "(set CATFISH_CONFIG env or place it in config/models.yaml)"
        )

    with open(path) as f:
        data = yaml.safe_load(f)
    return Config.model_validate(data)
