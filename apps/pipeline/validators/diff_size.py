"""Layer 5: diff size sanity — AI shouldn't rewrite the world for a 3-line fix."""
import re
import subprocess
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


SHORTSTAT_RE = re.compile(
    r"(?P<files>\d+) files? changed"
    r"(?:, (?P<insertions>\d+) insertions?\(\+\))?"
    r"(?:, (?P<deletions>\d+) deletions?\(\-\))?"
)


class DiffSizeValidator:
    name = "diff_size"

    # Tunable. Override in config for large refactor tasks.
    MAX_FILES = 8
    MAX_LINES_ADDED = 400
    MAX_LINES_REMOVED = 200

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()
        files, added, removed = self._shortstat(ctx)
        issues: list[ValidationIssue] = []

        if files > self.MAX_FILES:
            issues.append(
                ValidationIssue(
                    code="TOO_MANY_FILES",
                    message=f"AI touched {files} files (max {self.MAX_FILES}). "
                            "Scope may be too broad.",
                    severity=Severity.BLOCKER,
                )
            )
        if added > self.MAX_LINES_ADDED:
            issues.append(
                ValidationIssue(
                    code="DIFF_TOO_LARGE",
                    message=f"AI added {added} lines (max {self.MAX_LINES_ADDED}).",
                    severity=Severity.BLOCKER,
                )
            )
        if removed > self.MAX_LINES_REMOVED:
            issues.append(
                ValidationIssue(
                    code="TOO_MUCH_DELETION",
                    message=f"AI deleted {removed} lines (warning >{self.MAX_LINES_REMOVED}).",
                    severity=Severity.WARNING,
                )
            )

        duration = int((time.monotonic() - started) * 1000)
        log.info(
            "validator.diff_size.completed",
            files=files,
            added=added,
            removed=removed,
            issues=len(issues),
        )
        stats = {"files": files, "lines_added": added, "lines_removed": removed}
        if issues:
            result = ValidationResult.fail(self.name, issues, duration_ms=duration, stats=stats)
        else:
            result = ValidationResult.ok(self.name, duration_ms=duration, stats=stats)
        return result

    def _shortstat(self, ctx: ValidationContext) -> tuple[int, int, int]:
        try:
            output = subprocess.run(
                ["git", "diff", "--shortstat"],
                cwd=ctx.workspace_path,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            ).stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return (0, 0, 0)

        match = SHORTSTAT_RE.search(output)
        if not match:
            return (0, 0, 0)
        files = int(match.group("files") or 0)
        added = int(match.group("insertions") or 0)
        removed = int(match.group("deletions") or 0)
        return (files, added, removed)
