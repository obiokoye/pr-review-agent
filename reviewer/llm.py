"""LLM clients: one real client for every provider, plus a scripted fake.

Gemini, Groq, OpenAI and Anthropic all expose an OpenAI-compatible Chat
Completions endpoint, so one client covers all four. Switch with LLM_PROVIDER.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

Message = dict[str, str]


class LLM(Protocol):
    model: str

    def complete(self, messages: list[Message]) -> str: ...


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str | None
    key_env: str
    default_model: str
    json_mode: bool = True  # supports response_format={"type": "json_object"}


PROVIDERS: dict[str, Provider] = {
    # Free tier: https://aistudio.google.com/apikey
    "gemini": Provider(
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        "GEMINI_API_KEY",
        "gemini-2.5-flash",
    ),
    # Free tier: https://console.groq.com/keys
    "groq": Provider("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
    "openai": Provider("openai", None, "OPENAI_API_KEY", "gpt-4o-mini"),
    "anthropic": Provider(
        "anthropic", "https://api.anthropic.com/v1/", "ANTHROPIC_API_KEY", "claude-haiku-4-5", json_mode=False
    ),
}


class OpenAICompatibleLLM:
    def __init__(self, provider: Provider, api_key: str, model: str | None = None) -> None:
        from openai import OpenAI  # imported lazily so tests never need the SDK

        self.provider = provider
        self.model = model or provider.default_model
        self._client = OpenAI(api_key=api_key, base_url=provider.base_url, timeout=120)

    def complete(self, messages: list[Message]) -> str:
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": 0.2}
        if self.provider.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""


@dataclass
class FakeLLM:
    """Returns scripted replies. Used by the tests and by demo mode.

    `replies` is either a list (returned in order) or a function that picks a
    reply from the prompt, the way Http::fake() can take a callback in Laravel.
    """

    replies: list[str] | Callable[[list[Message]], str]
    model: str = "fake-llm"
    calls: list[list[Message]] = field(default_factory=list)

    def complete(self, messages: list[Message]) -> str:
        self.calls.append([dict(m) for m in messages])
        if callable(self.replies):
            return self.replies(messages)
        if not self.replies:
            raise RuntimeError("FakeLLM ran out of scripted replies")
        return self.replies.pop(0)


def is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def make_llm(env: dict[str, str] | None = None) -> tuple[LLM, bool]:
    """Build the client named by LLM_PROVIDER. Returns (llm, demo_mode).

    Falls back to demo mode (scripted replies) when DEMO_MODE=true or when the
    chosen provider has no API key, so the tool always runs.
    """
    env = dict(os.environ if env is None else env)
    name = env.get("LLM_PROVIDER", "gemini").strip().lower() or "gemini"
    if name not in PROVIDERS:
        raise SystemExit(f"Unknown LLM_PROVIDER '{name}'. Choose one of: {', '.join(PROVIDERS)}")
    provider = PROVIDERS[name]
    api_key = env.get(provider.key_env, "").strip()

    if is_truthy(env.get("DEMO_MODE")) or not api_key:
        from .demo import demo_llm

        return demo_llm(), True
    return OpenAICompatibleLLM(provider, api_key, env.get("LLM_MODEL") or None), False
