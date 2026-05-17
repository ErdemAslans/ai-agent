"""Layer 2: changed files parse as valid source."""
import ast
import subprocess
import time
from pathlib import Path

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


def _check_python(path: Path) -> ValidationIssue | None:
    try:
        ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError as exc:
        return ValidationIssue(
            code="SYNTAX_ERROR",
            message=f"Python syntax error: {exc.msg}",
            severity=Severity.BLOCKER,
            file=str(path),
            line=exc.lineno,
        )
    except (UnicodeDecodeError, OSError) as exc:
        return ValidationIssue(
            code="FILE_READ_ERROR",
            message=f"Cannot read file: {exc}",
            severity=Severity.WARNING,
            file=str(path),
        )
    return None


def _check_node(path: Path) -> ValidationIssue | None:
    try:
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # node not installed — skip silently
    if result.returncode != 0:
        return ValidationIssue(
            code="SYNTAX_ERROR",
            message=f"JavaScript syntax error: {result.stderr.decode(errors='ignore')[:200]}",
            severity=Severity.BLOCKER,
            file=str(path),
        )
    return None


CHECKERS = {
    ".py": _check_python,
    ".js": _check_node,
    ".mjs": _check_node,
    ".cjs": _check_node,
}


class SyntaxValidator:
    name = "syntax_check"

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        issues: list[ValidationIssue] = []

        for rel in ctx.changed_files:
            full = ctx.workspace_path / rel
            if not full.exists():
                continue  # file may have been deleted, no syntax to check
            checker = CHECKERS.get(full.suffix)
            if not checker:
                continue
            issue = checker(full)
            if issue:
                issue.file = rel  # use relative path
                issues.append(issue)

        duration = int((time.monotonic() - started) * 1000)
        log.info("validator.syntax.completed", passed=not issues, issues=len(issues))
        if issues:
            return ValidationResult.fail(self.name, issues, duration_ms=duration)
        return ValidationResult.ok(self.name, duration_ms=duration)
