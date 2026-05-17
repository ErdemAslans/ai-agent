"""Layer 6: run the project's tests.

Day 3: subprocess-based runner. Installs requirements.txt on demand and
executes the analyzer-detected test command in the workspace.

Day 4: replace with Docker sandbox (network=none, memory cap).
"""
import shlex
import subprocess
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


class TestRunner:
    name = "test_runner"

    # Per-run caps so a hostile or runaway test cannot stall the pipeline.
    INSTALL_TIMEOUT = 180
    TEST_TIMEOUT = 120

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()

        if not ctx.test_command:
            issue = ValidationIssue(
                code="NO_TEST_COMMAND",
                message="Analyzer could not detect a test command.",
                severity=Severity.WARNING,
            )
            duration = int((time.monotonic() - started) * 1000)
            return ValidationResult.fail(self.name, [issue], duration_ms=duration,
                                         test_output="", test_status="skipped")

        # 1. Install dependencies if a Python requirements file is present
        install_log = self._install_python_deps(ctx)

        # 2. Run the test command itself
        try:
            cmd = shlex.split(ctx.test_command)
            run = subprocess.run(
                cmd,
                cwd=ctx.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.TEST_TIMEOUT,
            )
            stdout = run.stdout or ""
            stderr = run.stderr or ""
            output = (stdout + stderr)[:8000]  # cap stored size
            duration = int((time.monotonic() - started) * 1000)
            log.info(
                "validator.test_runner.completed",
                exit_code=run.returncode,
                duration_ms=duration,
            )
            if run.returncode == 0:
                return ValidationResult.ok(
                    self.name,
                    duration_ms=duration,
                    test_status="passed",
                    test_output=output,
                    install_output=install_log,
                )
            return ValidationResult.fail(
                self.name,
                [
                    ValidationIssue(
                        code="TESTS_FAILED",
                        message=f"Tests exited with code {run.returncode}",
                        severity=Severity.BLOCKER,
                    )
                ],
                duration_ms=duration,
                test_status="failed",
                test_output=output,
                install_output=install_log,
            )
        except subprocess.TimeoutExpired:
            duration = int((time.monotonic() - started) * 1000)
            log.warning("validator.test_runner.timeout", duration_ms=duration)
            return ValidationResult.fail(
                self.name,
                [
                    ValidationIssue(
                        code="TEST_TIMEOUT",
                        message=f"Tests did not finish within {self.TEST_TIMEOUT}s",
                        severity=Severity.BLOCKER,
                    )
                ],
                duration_ms=duration,
                test_status="timeout",
                test_output="",
            )

    def _install_python_deps(self, ctx: ValidationContext) -> str:
        req = ctx.workspace_path / "requirements.txt"
        if not req.exists():
            return ""
        try:
            res = subprocess.run(
                ["pip", "install", "--user", "--quiet",
                 "--disable-pip-version-check", "-r", "requirements.txt"],
                cwd=ctx.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.INSTALL_TIMEOUT,
            )
            if res.returncode != 0:
                log.warning("validator.test_runner.install_failed",
                            stderr=res.stderr[:500])
            return (res.stdout + res.stderr)[:2000]
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            log.warning("validator.test_runner.install_error", error=str(exc))
            return f"install error: {exc}"
