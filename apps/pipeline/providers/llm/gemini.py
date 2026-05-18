"""GeminiProvider — Google Gemini API via google-genai SDK (sync)."""
import time

import structlog
from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError

from .base import LLMResponse, LLMUsage

log = structlog.get_logger(__name__)

# Backoff for transient Gemini failures (503 overload, 429 rate limit).
# google-genai retries some internally; this is our outer safety net.
TRANSIENT_RETRY_DELAYS = (2, 5, 12)

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
        json_mode: bool = False,
        response_schema: object | None = None,
    ) -> LLMResponse:
        log.info(
            "llm.generate.started",
            provider=self.name,
            model=model,
            prompt_chars=len(prompt),
            json_mode=json_mode,
            has_schema=response_schema is not None,
        )

        config_kwargs: dict = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "system_instruction": system,
        }
        if json_mode:
            # Force the model to return strictly valid JSON.
            config_kwargs["response_mime_type"] = "application/json"
            if response_schema is not None:
                # Pydantic class — Gemini validates the response against it.
                # This eliminates malformed-JSON failure modes (unescaped
                # quotes inside string values, missing delimiters, etc.).
                config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        response = None
        last_exc: Exception | None = None
        for attempt, delay in enumerate((0,) + TRANSIENT_RETRY_DELAYS):
            if delay:
                log.warning(
                    "llm.generate.retry_after_transient",
                    attempt=attempt,
                    delay_seconds=delay,
                    last_error=str(last_exc)[:200],
                )
                time.sleep(delay)
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
                break
            except ServerError as exc:
                # 5xx — Gemini is unavailable / overloaded; retry.
                last_exc = exc
                continue
            except ClientError as exc:
                # Only retry rate-limit (429); other 4xx are permanent.
                if getattr(exc, "code", None) == 429:
                    last_exc = exc
                    continue
                raise
        if response is None:
            assert last_exc is not None
            raise last_exc

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
