"""Layer 7: AI Self-Review — does the diff actually satisfy each acceptance criterion?

A cheap secondary LLM pass (Gemini Flash) that judges the diff against the AC list.
Catches cases where syntax/tests pass but the change misses the requirement.
"""
import json
import re
import time
from typing import Any

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


SYSTEM_PROMPT = """You are a strict code-review judge.

Given a unified diff and a list of acceptance criteria, decide for EACH
criterion whether the diff satisfies it.

OUTPUT FORMAT — strict JSON only, no markdown fences, no preamble:
{
  "criteria": [
    {"text": "...", "met": true|false, "evidence": "short snippet or path", "reasoning": "1 sentence"}
  ]
}

Rules:
- Be conservative. If you cannot find evidence in the diff, mark met=false.
- "Existing flow continues working" should be considered NOT MET only if
  the diff visibly removes or breaks the existing behavior.
- Do not invent files or lines that aren't in the diff.
"""


def _extract_json(text: str) -> dict[str, Any]:
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if fence:
        return json.loads(fence.group(1))
    obj = re.search(r"\{[\s\S]*\}", text)
    if not obj:
        raise ValueError(f"No JSON in self-review output: {text[:200]!r}")
    return json.loads(obj.group(0))


class AISelfReviewer:
    name = "ai_self_review"

    def __init__(self, llm=None, model: str = "gemini-2.5-flash"):
        self.llm = llm
        self.model = model

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        if not self.llm or ctx.llm_provider is not None:
            llm = ctx.llm_provider or self.llm
        else:
            llm = self.llm

        if llm is None:
            issue = ValidationIssue(
                code="NO_LLM",
                message="AI self-reviewer has no LLM provider configured.",
                severity=Severity.WARNING,
            )
            duration = int((time.monotonic() - started) * 1000)
            return ValidationResult.fail(self.name, [issue], duration_ms=duration)

        criteria_list = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(ctx.acceptance_criteria))
        diff_trimmed = ctx.diff[:8000]  # keep prompt cheap

        prompt = (
            f"ACCEPTANCE CRITERIA:\n{criteria_list}\n\n"
            f"DIFF:\n{diff_trimmed}\n\n"
            "Output JSON per system instructions."
        )

        response = llm.generate(
            prompt=prompt,
            model=self.model,
            system=SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=2048,
        )

        try:
            data = _extract_json(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            duration = int((time.monotonic() - started) * 1000)
            return ValidationResult.fail(
                self.name,
                [ValidationIssue(
                    code="SELF_REVIEW_PARSE_ERROR",
                    message=f"Could not parse self-review output: {exc}",
                    severity=Severity.WARNING,
                )],
                duration_ms=duration,
                criteria=[],
            )

        criteria = data.get("criteria", [])
        issues = []
        for item in criteria:
            if not item.get("met"):
                issues.append(
                    ValidationIssue(
                        code="AC_NOT_MET",
                        message=(
                            f"Acceptance criterion not satisfied: '{item.get('text', '')}'. "
                            f"Reason: {item.get('reasoning', '(none)')}"
                        ),
                        severity=Severity.BLOCKER,
                    )
                )

        duration = int((time.monotonic() - started) * 1000)
        log.info(
            "validator.ai_self_review.completed",
            criteria_total=len(criteria),
            criteria_met=sum(1 for c in criteria if c.get("met")),
            tokens=response.usage.prompt_tokens + response.usage.completion_tokens,
        )
        return ValidationResult(
            name=self.name,
            passed=not issues,
            issues=issues,
            duration_ms=duration,
            extra={
                "criteria": criteria,
                "criteria_total": len(criteria),
                "criteria_met": sum(1 for c in criteria if c.get("met")),
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost_usd": response.usage.estimated_cost_usd,
            },
        )
