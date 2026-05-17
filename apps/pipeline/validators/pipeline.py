"""ValidationPipeline — orchestrates all layers.

Phase 1: layers 1-5 run in parallel (deterministic, fast).
Phase 2: layer 6 (TestRunner) runs sequentially.
Phase 3: layer 7 (AISelfReviewer) runs only after tests pass.
"""
from concurrent.futures import ThreadPoolExecutor

import structlog

from .base import ValidationContext, ValidationResult, Validator

log = structlog.get_logger(__name__)


class ValidationPipeline:
    def __init__(
        self,
        deterministic_validators: list[Validator],
        test_runner: Validator | None = None,
        ai_reviewer: Validator | None = None,
    ):
        self.deterministic = deterministic_validators
        self.test_runner = test_runner
        self.ai_reviewer = ai_reviewer

    def run_pre_test(self, ctx: ValidationContext) -> list[ValidationResult]:
        """Run deterministic checks in parallel. Returns one result per validator."""
        log.info("validation.pre_test.started", count=len(self.deterministic))
        with ThreadPoolExecutor(max_workers=max(1, len(self.deterministic))) as pool:
            results = list(pool.map(lambda v: v.validate(ctx), self.deterministic))
        log.info(
            "validation.pre_test.completed",
            passed=all(r.passed for r in results),
            results={r.name: r.passed for r in results},
        )
        return results

    def run_test(self, ctx: ValidationContext) -> ValidationResult | None:
        if self.test_runner is None:
            return None
        return self.test_runner.validate(ctx)

    def run_post_test(self, ctx: ValidationContext) -> ValidationResult | None:
        if self.ai_reviewer is None:
            return None
        return self.ai_reviewer.validate(ctx)


def summarize(results: list[ValidationResult]) -> dict:
    """Serialize validator results for ExecutionReport.validation_summary."""
    return {r.name: r.to_dict() for r in results}
