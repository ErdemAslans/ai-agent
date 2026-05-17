"""Layer 3: AI output must not contain leaked credentials.

Patterns derived from Gitleaks default ruleset (subset). Scans ADDED lines
in the diff only — pre-existing secrets are not the agent's problem.
"""
import re
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{32,}"), "OpenAI API key"),
    (re.compile(r"sk-ant-[a-zA-Z0-9-_]{20,}"), "Anthropic API key"),
    (re.compile(r"github_pat_[A-Z0-9_]{20,}", re.IGNORECASE), "GitHub fine-grained PAT"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub classic PAT"),
    (re.compile(r"ghs_[A-Za-z0-9]{30,}"), "GitHub server token"),
    (re.compile(r"AIza[A-Za-z0-9_-]{35}"), "Google API key"),
    (re.compile(r"AKIA[A-Z0-9]{16}"), "AWS access key id"),
    (re.compile(r"sk_live_[a-zA-Z0-9]{24,}"), "Stripe live key"),
    (re.compile(r'(?i)password\s*[:=]\s*["\'][^"\']{8,}'), "Hardcoded password"),
    (re.compile(r'(?i)secret\s*[:=]\s*["\'][^"\']{8,}'), "Hardcoded secret literal"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "PEM private key"),
]


class SecretScanner:
    name = "secret_scan"

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        issues: list[ValidationIssue] = []

        # Scan only ADDED lines in the unified diff
        for line in ctx.diff.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            payload = line[1:]
            for pattern, label in PATTERNS:
                if pattern.search(payload):
                    issues.append(
                        ValidationIssue(
                            code="SECRET_LEAK",
                            message=f"Possible secret detected ({label})",
                            severity=Severity.BLOCKER,
                        )
                    )
                    break  # one issue per line is enough

        duration = int((time.monotonic() - started) * 1000)
        log.info("validator.secret_scan.completed", passed=not issues, issues=len(issues))
        if issues:
            return ValidationResult.fail(self.name, issues, duration_ms=duration)
        return ValidationResult.ok(self.name, duration_ms=duration)
