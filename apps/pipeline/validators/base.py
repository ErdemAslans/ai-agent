"""Validator protocol, ValidationResult, and ValidationContext."""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: Severity = Severity.BLOCKER
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "file": self.file,
            "line": self.line,
        }


@dataclass
class ValidationResult:
    name: str
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, name: str, duration_ms: int = 0, **extra) -> "ValidationResult":
        return cls(name=name, passed=True, duration_ms=duration_ms, extra=extra)

    @classmethod
    def fail(
        cls,
        name: str,
        issues: list[ValidationIssue],
        duration_ms: int = 0,
        **extra,
    ) -> "ValidationResult":
        passed = not any(i.severity == Severity.BLOCKER for i in issues)
        return cls(
            name=name,
            passed=passed,
            issues=issues,
            duration_ms=duration_ms,
            extra=extra,
        )

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "issues": [i.to_dict() for i in self.issues],
            "extra": self.extra,
        }


@dataclass
class ValidationContext:
    """Carried through all validators."""

    workspace_path: Path
    allowlist: set[str]
    requirement: str
    acceptance_criteria: list[str]
    changed_files: list[str]
    diff: str
    test_command: str
    llm_provider: Any | None = None  # only used by AISelfReviewer


class Validator(Protocol):
    name: str

    def validate(self, ctx: ValidationContext) -> ValidationResult: ...
