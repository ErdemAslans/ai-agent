"""LLMProvider abstraction — swap providers (Gemini, Claude, OpenAI, Ollama) via config."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)


class LLMProvider(Protocol):
    """Sync interface. Async variant can be added later if Celery moves to async."""

    name: str

    def generate(
        self,
        prompt: str,
        model: str,
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = False,
        response_schema: object | None = None,
    ) -> LLMResponse: ...
