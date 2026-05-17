"""Layer 1: only files in the allowlist may be modified."""
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


class FileAllowlistValidator:
    name = "file_allowlist"

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        issues: list[ValidationIssue] = []

        for f in ctx.changed_files:
            if f not in ctx.allowlist:
                issues.append(
                    ValidationIssue(
                        code="UNAUTHORIZED_FILE",
                        message=(
                            f"AI modified a file outside the relevant-files allowlist: {f}"
                        ),
                        severity=Severity.BLOCKER,
                        file=f,
                    )
                )

        duration = int((time.monotonic() - started) * 1000)
        log.info(
            "validator.file_allowlist.completed",
            passed=not issues,
            issues=len(issues),
            changed_files=len(ctx.changed_files),
        )
        if issues:
            return ValidationResult.fail(self.name, issues, duration_ms=duration)
        return ValidationResult.ok(self.name, duration_ms=duration)
