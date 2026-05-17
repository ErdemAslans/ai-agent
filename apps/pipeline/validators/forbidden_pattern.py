"""Layer 4: dangerous code patterns must not appear in AI-added lines."""
import re
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


# (regex, message)
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bos\.system\s*\("), "os.system — use subprocess with args list"),
    (re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True"), "subprocess shell=True"),
    (re.compile(r"\beval\s*\("), "eval — arbitrary code execution"),
    (re.compile(r"\bexec\s*\("), "exec — arbitrary code execution"),
    (re.compile(r"__import__\s*\("), "dynamic __import__"),
    (re.compile(r"pickle\.loads\s*\("), "pickle deserialization"),
    (re.compile(r"rm\s+-rf\s+/"), "rm -rf / pattern"),
    (re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE), "SQL DROP TABLE"),
    (re.compile(r"\bDELETE\s+FROM\b[\s\S]{0,200}\bWHERE\s+1\s*=\s*1", re.IGNORECASE),
     "unrestricted SQL DELETE"),
]


class ForbiddenPatternValidator:
    name = "forbidden_pattern"

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        issues: list[ValidationIssue] = []

        for line in ctx.diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            payload = line[1:]
            for pattern, message in PATTERNS:
                if pattern.search(payload):
                    issues.append(
                        ValidationIssue(
                            code="FORBIDDEN_PATTERN",
                            message=message,
                            severity=Severity.BLOCKER,
                        )
                    )
                    break

        duration = int((time.monotonic() - started) * 1000)
        log.info(
            "validator.forbidden_pattern.completed",
            passed=not issues,
            issues=len(issues),
        )
        if issues:
            return ValidationResult.fail(self.name, issues, duration_ms=duration)
        return ValidationResult.ok(self.name, duration_ms=duration)
