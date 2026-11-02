"""Pluggable LLM backend with streaming. Switch with LLM_PROVIDER=groq|claude."""
from __future__ import annotations

from typing import Iterator, Protocol

from .config import Settings


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...
    def stream(self, system: str, user: str) -> Iterator[str]: ...


class GroqLLM:
    def __init__(self, api_key: str, model: str, temperature: float, reasoning_effort: str = "low"):
        from groq import Groq

        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set. Get a free key at console.groq.com.")
        self.client = Groq(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.name = f"groq/{model}"

    def _kwargs(self, system, user):
        kw = dict(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if "gpt-oss" in self.model:
            kw["reasoning_effort"] = self.reasoning_effort
        return kw

    def complete(self, system, user):
        resp = self.client.chat.completions.create(**self._kwargs(system, user))
        return resp.choices[0].message.content or ""

    def stream(self, system, user):
        for part in self.client.chat.completions.create(**self._kwargs(system, user), stream=True):
            delta = part.choices[0].delta.content if part.choices else None
            if delta:
                yield delta


class ClaudeLLM:
    def __init__(self, api_key: str, model: str, temperature: float):
        import anthropic

        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.name = f"anthropic/{model}"

    def complete(self, system, user):
        return "".join(self.stream(system, user))

    def stream(self, system, user):
        with self.client.messages.stream(
            model=self.model, max_tokens=2048, temperature=self.temperature,
            system=system, messages=[{"role": "user", "content": user}],
        ) as s:
            yield from s.text_stream


def build_llm(s: Settings) -> LLM:
    if s.llm_provider == "claude":
        return ClaudeLLM(s.anthropic_api_key, s.claude_model, s.temperature)
    if s.llm_provider == "groq":
        return GroqLLM(s.groq_api_key, s.groq_model, s.temperature, s.groq_reasoning_effort)
    raise RuntimeError(f"Unknown LLM_PROVIDER '{s.llm_provider}' (use groq or claude)")
