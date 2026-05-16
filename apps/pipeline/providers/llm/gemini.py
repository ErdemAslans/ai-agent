"""GeminiProvider — Google Gemini API via google-genai SDK (sync)."""
import structlog
from google import genai
from google.genai import types

from .base import LLMResponse, LLMUsage

log = structlog.get_logger(__name__)

# Approximate per-1M-token pricing as of May 2026.
# Free tier covers development; these numbers populate cost tracking only.
PRICING = {
    "gemini-2.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
}


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is empty — set it in .env")
        self.client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt: str,
        model: str = "gemini-2.5-flash",
        system: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        log.info("llm.generate.started", provider=self.name, model=model, prompt_chars=len(prompt))

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens,
            system_instruction=system,
        )

        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

        prompt_tokens = 0
        completion_tokens = 0
        if response.usage_metadata is not None:
            prompt_tokens = response.usage_metadata.prompt_token_count or 0
            completion_tokens = response.usage_metadata.candidates_token_count or 0

        cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
        text = response.text or ""

        log.info(
            "llm.generate.completed",
            provider=self.name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
        )

        return LLMResponse(
            text=text,
            model=model,
            usage=LLMUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_usd=cost,
            ),
        )

    @staticmethod
    def _calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = PRICING.get(model, {"input": 0.0, "output": 0.0})
        return (prompt_tokens * pricing["input"] / 1_000_000) + (
            completion_tokens * pricing["output"] / 1_000_000
        )
