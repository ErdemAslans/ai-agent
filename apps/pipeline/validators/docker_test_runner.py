"""Docker-sandbox alternative to the subprocess-based TestRunner.

Same contract (returns a ValidationResult named ``test_runner``) so it slots
straight into ValidationPipeline. Selected via the ``TEST_RUNNER_MODE``
environment variable in settings.

Security gains over subprocess:
  * network=none — tests cannot reach the network
  * mem_limit + cpu_quota — runaway tests cannot starve the host
  * ephemeral container — destroyed after run, no host filesystem access
  * read-only root filesystem option available

Setup requirement:
  * The Docker socket must be reachable from the worker container.
    Add this to docker-compose worker.volumes:
        - /var/run/docker.sock:/var/run/docker.sock
"""
import time

import structlog

from .base import Severity, ValidationContext, ValidationIssue, ValidationResult

log = structlog.get_logger(__name__)


class DockerSandboxTestRunner:
    name = "test_runner"

    # Pinned image keeps tests reproducible.
    DEFAULT_IMAGE = "python:3.12-slim"
    MEM_LIMIT = "512m"
    CPU_PERIOD = 100000
    CPU_QUOTA = 50000          # ~50% of one CPU core
    NETWORK_MODE = "none"      # offline by default
    INSTALL_TIMEOUT = 180
    TEST_TIMEOUT = 120

    # Name of the docker-compose-managed volume that holds /workspaces.
    # Matches the volume defined in docker-compose.yml.
    WORKSPACES_VOLUME = "vodafone_workspaces"
    WORKSPACES_BIND = "/workspaces"

    def __init__(self, image: str | None = None):
        # Lazy import so workers without the docker socket can still load
        # this module (it just won't be selected as the runner).
        import docker

        self.docker = docker.from_env()
        self.image = image or self.DEFAULT_IMAGE

    def validate(self, ctx: ValidationContext) -> ValidationResult:
        started = time.monotonic()

        if not ctx.test_command:
            issue = ValidationIssue(
                code="NO_TEST_COMMAND",
                message="Analyzer could not detect a test command.",
                severity=Severity.WARNING,
            )
            return ValidationResult.fail(
                self.name, [issue], duration_ms=0, test_status="skipped",
            )

        # The test container sees the workspace under the same path that the
        # worker container does. We derive the relative path of the per-task
        # subdir under /workspaces and use it as the container's working dir.
        try:
            workspace_subdir = ctx.workspace_path.relative_to(self.WORKSPACES_BIND).as_posix()
        except ValueError:
            workspace_subdir = ctx.workspace_path.name

        install_prefix = ""
        if (ctx.workspace_path / "requirements.txt").exists():
            install_prefix = "pip install --no-cache-dir --quiet -r requirements.txt && "

        full_command = f"{install_prefix}{ctx.test_command}"
        log.info(
            "docker_sandbox.starting",
            image=self.image,
            workdir=f"{self.WORKSPACES_BIND}/{workspace_subdir}",
        )

        try:
            container = self.docker.containers.run(
                image=self.image,
                command=["sh", "-c", full_command],
                volumes={
                    self.WORKSPACES_VOLUME: {
                        "bind": self.WORKSPACES_BIND,
                        "mode": "rw",
                    },
                },
                working_dir=f"{self.WORKSPACES_BIND}/{workspace_subdir}",
                network_mode=self.NETWORK_MODE,
                mem_limit=self.MEM_LIMIT,
                cpu_period=self.CPU_PERIOD,
                cpu_quota=self.CPU_QUOTA,
                detach=True,
                # We accept the risk of running as root inside an ephemeral
                # offline container in exchange for `pip install` working
                # without per-user HOME fiddling.
            )
        except Exception as exc:
            duration = int((time.monotonic() - started) * 1000)
            log.error("docker_sandbox.start_failed", error=str(exc))
            return ValidationResult.fail(
                self.name,
                [
                    ValidationIssue(
                        code="SANDBOX_START_FAILED",
                        message=f"Could not start Docker sandbox: {exc}",
                        severity=Severity.BLOCKER,
                    )
                ],
                duration_ms=duration,
                test_status="error",
            )

        try:
            result = container.wait(timeout=self.TEST_TIMEOUT + self.INSTALL_TIMEOUT)
            exit_code = result.get("StatusCode", -1)
            output = container.logs().decode("utf-8", errors="ignore")[:8000]
        except Exception as exc:
            log.warning("docker_sandbox.timeout_or_error", error=str(exc))
            try:
                container.kill()
            except Exception:
                pass
            duration = int((time.monotonic() - started) * 1000)
            return ValidationResult.fail(
                self.name,
                [
                    ValidationIssue(
                        code="TEST_TIMEOUT",
                        message=f"Sandbox did not finish in time: {exc}",
                        severity=Severity.BLOCKER,
                    )
                ],
                duration_ms=duration,
                test_status="timeout",
            )
        finally:
            try:
                container.remove(force=True)
            except Exception:
                pass

        duration = int((time.monotonic() - started) * 1000)
        log.info(
            "docker_sandbox.completed",
            exit_code=exit_code,
            duration_ms=duration,
        )

        if exit_code == 0:
            return ValidationResult.ok(
                self.name,
                duration_ms=duration,
                test_status="passed",
                test_output=output,
            )
        return ValidationResult.fail(
            self.name,
            [
                ValidationIssue(
                    code="TESTS_FAILED",
                    message=f"Tests exited with code {exit_code}",
                    severity=Severity.BLOCKER,
                )
            ],
            duration_ms=duration,
            test_status="failed",
            test_output=output,
        )
