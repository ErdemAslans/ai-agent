"""Orchestrator Celery task — wires agents + validators + providers.

Pipeline (Day 3):
  1.  Parse task description
  2.  Check repo allowlist
  3.  Create workspace + clone repo
  4.  Analyze repo (language, test cmd, relevant files)
  5.  Code Writer agent (Gemini)
  6.  Pre-test validation pipeline (5 deterministic checks, parallel)
  7.  Run tests; on failure run Test Fixer agent (max 2 retries)
  8.  AI Self-Review against acceptance criteria
  9.  PR Writer assembles the body
 10.  Either dry-run output OR commit/push/open PR
 11.  Update ExecutionReport
"""
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import fnmatch
import structlog
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.tasks.models import AgentRun, ExecutionReport, Task

from .agents.code_writer import CodeWriterAgent, CodeWriterInput
from .agents.pr_writer import PRWriterAgent, PRWriterInput
from .agents.repo_analyzer import RepoAnalyzerAgent, RepoAnalyzerInput
from .agents.task_parser import TaskParserAgent, TaskParserInput
from .agents.test_fixer import TestFixerAgent, TestFixerInput
from .observability import langfuse_context, observe
from .providers.git.github import GitHubProvider
from .providers.llm.gemini import GeminiProvider
from .validators.ai_self_review import AISelfReviewer
from .validators.base import Severity, ValidationContext
from .validators.diff_size import DiffSizeValidator
from .validators.file_allowlist import FileAllowlistValidator
from .validators.forbidden_pattern import ForbiddenPatternValidator
from .validators.pipeline import ValidationPipeline, summarize
from .validators.secret_scanner import SecretScanner
from .validators.syntax import SyntaxValidator
from .validators.test_runner import TestRunner


def _build_test_runner():
    """Pick TestRunner implementation based on settings.

    "subprocess" — runs pytest in the worker container (default, fast, no extra setup).
    "docker"     — spawns an ephemeral Docker container (network=none, mem-limited).
    """
    mode = (settings.TEST_RUNNER_MODE or "subprocess").lower()
    if mode == "docker":
        try:
            from .validators.docker_test_runner import DockerSandboxTestRunner
            log.info("test_runner.mode", mode="docker")
            return DockerSandboxTestRunner()
        except Exception as exc:
            log.warning(
                "test_runner.docker_unavailable",
                error=str(exc),
                fallback="subprocess",
            )
    log.info("test_runner.mode", mode="subprocess")
    return TestRunner()
from .workspace import WorkspaceManager

log = structlog.get_logger(__name__)

MAX_TEST_RETRIES = 2


def _check_repo_allowed(repo_url: str) -> None:
    allowlist = settings.REPOSITORY_ALLOWLIST
    if not allowlist:
        log.warning("repo.allowlist.empty", repo_url=repo_url, note="dev mode")
        return
    for pattern in allowlist:
        if fnmatch.fnmatch(repo_url, pattern) or fnmatch.fnmatch(repo_url, f"*{pattern}*"):
            return
    raise PermissionError(f"Repository '{repo_url}' is not in REPOSITORY_ALLOWLIST")


def _build_file_tree(workspace: Path, max_entries: int = 200) -> list[str]:
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", "target", "vendor"}
    entries: list[str] = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in skip_dirs):
            continue
        entries.append(path.relative_to(workspace).as_posix())
        if len(entries) >= max_entries:
            entries.append("... (truncated)")
            break
    return entries


def _append_timeline(report: ExecutionReport, step: str, duration_ms: int,
                     status: str = "ok") -> None:
    report.refresh_from_db()
    report.timeline.append({
        "step": step,
        "at": datetime.now(dt_timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_ms": duration_ms,
        "status": status,
    })
    report.save(update_fields=["timeline"])


def _save_validation_summary(report: ExecutionReport, name: str, result_dict: dict) -> None:
    report.refresh_from_db()
    report.validation_summary[name] = result_dict
    report.save(update_fields=["validation_summary"])


def _fail(task: Task, report: ExecutionReport, error: str) -> None:
    report.error = error
    report.status = ExecutionReport.Status.FAILED
    report.completed_at = timezone.now()
    report.save()
    task.status = Task.Status.FAILED
    task.save(update_fields=["status"])


@shared_task(bind=True, name="apps.pipeline.orchestrator.orchestrate")
@observe(name="orchestrate", capture_input=False, capture_output=False)
def orchestrate(self, task_uuid: str, trace_id: str) -> dict:
    task = Task.objects.get(id=task_uuid)
    report = ExecutionReport.objects.get(trace_id=trace_id)

    langfuse_context.update_current_trace(
        name=f"task:{task.task_id}",
        session_id=trace_id,
        metadata={
            "task_id": task.task_id,
            "trace_id": trace_id,
            "title": task.title,
            "source": task.source,
        },
        tags=["ai-agent", task.source],
    )

    log.info("orchestrator.start", task_id=task.task_id, trace_id=trace_id)
    task.status = Task.Status.RUNNING
    task.save(update_fields=["status"])
    report.status = ExecutionReport.Status.RUNNING
    report.save(update_fields=["status"])

    try:
        llm = GeminiProvider(api_key=settings.GEMINI_API_KEY)
        git = GitHubProvider(
            token=settings.GITHUB_TOKEN,
            token_map=settings.GITHUB_TOKEN_MAP,
        )
        workspace_mgr = WorkspaceManager()
    except ValueError as exc:
        log.error("orchestrator.config_missing", error=str(exc))
        _fail(task, report, f"Configuration error: {exc}")
        raise

    pipeline = ValidationPipeline(
        deterministic_validators=[
            FileAllowlistValidator(),
            SyntaxValidator(),
            SecretScanner(),
            ForbiddenPatternValidator(),
            DiffSizeValidator(),
        ],
        test_runner=_build_test_runner(),
        ai_reviewer=AISelfReviewer(llm=llm),
    )

    workspace_path: Path | None = None

    try:
        # ---- 1. Parse ----
        t0 = time.monotonic()
        parsed = TaskParserAgent().run(
            TaskParserInput(
                raw_task_id=task.task_id,
                raw_title=task.title,
                raw_description=task.description,
            ),
            report=report,
        )
        _append_timeline(report, "task_parsed", int((time.monotonic() - t0) * 1000))

        task.repository_url = parsed.repository_url
        task.base_branch = parsed.base_branch
        task.requirement = parsed.requirement
        task.acceptance_criteria = parsed.acceptance_criteria
        task.save()

        # ---- 2. Allowlist ----
        _check_repo_allowed(parsed.repository_url)

        # ---- 3. Workspace + clone ----
        t0 = time.monotonic()
        workspace_path = workspace_mgr.create(task.task_id, trace_id)
        report.workspace_path = str(workspace_path)
        report.save(update_fields=["workspace_path"])
        _append_timeline(report, "workspace_created", int((time.monotonic() - t0) * 1000))

        t0 = time.monotonic()
        git.clone(parsed.repository_url, parsed.base_branch, workspace_path)
        _append_timeline(report, "clone_completed", int((time.monotonic() - t0) * 1000))

        # ---- 4. Analyze ----
        t0 = time.monotonic()
        analysis = RepoAnalyzerAgent().run(
            RepoAnalyzerInput(
                workspace_path=str(workspace_path),
                requirement=parsed.requirement,
            ),
            report=report,
        )
        _append_timeline(report, "analysis_completed", int((time.monotonic() - t0) * 1000))

        if not analysis.relevant_files:
            raise RuntimeError(
                "RepoAnalyzer found no relevant files. Refusing to let AI guess targets."
            )

        # ---- 5. CodeWriter ----
        t0 = time.monotonic()
        writer = CodeWriterAgent(llm=llm)
        code_out = writer.run(
            CodeWriterInput(
                workspace_path=str(workspace_path),
                requirement=parsed.requirement,
                acceptance_criteria=parsed.acceptance_criteria,
                relevant_files=analysis.relevant_files,
                file_tree=_build_file_tree(workspace_path),
                language=analysis.language,
                framework=analysis.framework,
            ),
            report=report,
        )
        _append_timeline(report, "code_changed", int((time.monotonic() - t0) * 1000))

        # ---- 5.5 Build validation context ----
        diff_text = git.diff(workspace_path)
        ctx = ValidationContext(
            workspace_path=workspace_path,
            allowlist=set(analysis.relevant_files),
            requirement=parsed.requirement,
            acceptance_criteria=parsed.acceptance_criteria,
            changed_files=[f.path for f in code_out.files],
            diff=diff_text,
            test_command=analysis.test_command,
            llm_provider=llm,
        )

        # ---- 6. Pre-test validation pipeline (parallel) ----
        t0 = time.monotonic()
        pre_results = pipeline.run_pre_test(ctx)
        for r in pre_results:
            _save_validation_summary(report, r.name, r.to_dict())
        _append_timeline(
            report,
            "pre_test_validation",
            int((time.monotonic() - t0) * 1000),
            status="ok" if all(r.passed for r in pre_results) else "failed",
        )
        if not all(r.passed for r in pre_results):
            blocking = [
                i.message
                for r in pre_results for i in r.issues
                if i.severity == Severity.BLOCKER
            ]
            raise RuntimeError(
                "Pre-test validation rejected the AI output:\n- " + "\n- ".join(blocking)
            )

        # ---- 7. Tests + retry loop ----
        t0 = time.monotonic()
        test_result = pipeline.run_test(ctx)
        retries = 0
        while (
            test_result is not None
            and not test_result.passed
            and retries < MAX_TEST_RETRIES
        ):
            failing_output = test_result.extra.get("test_output", "")
            log.info(
                "tests.retry",
                attempt=retries + 1,
                max=MAX_TEST_RETRIES,
                output_chars=len(failing_output),
            )

            try:
                TestFixerAgent(llm=llm).run(
                    TestFixerInput(
                        workspace_path=str(workspace_path),
                        test_output=failing_output,
                        last_changed_files=[f.path for f in code_out.files],
                        allowlist=analysis.relevant_files,
                        requirement=parsed.requirement,
                    ),
                    report=report,
                )
            except Exception as exc:
                # TestFixer failed to land a usable patch for any reason:
                # unparseable LLM JSON, rejected paths, or upstream Gemini
                # 503/429 even after our backoff. Either way the workspace
                # is unchanged, so re-running tests would yield the same
                # result. Stop retrying and proceed with the existing failing
                # test_result — the PR opens with the tests-failed marker
                # per Section 4.6 instead of crashing the whole pipeline.
                log.warning(
                    "test_fixer.attempt_failed",
                    attempt=retries + 1,
                    error=str(exc),
                    note="Skipping further retries; opening PR with tests-failed flag",
                )
                break

            # Refresh diff after the fix and re-run tests.
            # changed_files stays the same — TestFixer enforces the same allowlist.
            ctx.diff = git.diff(workspace_path)
            test_result = pipeline.run_test(ctx)
            retries += 1

        if test_result is not None:
            _save_validation_summary(report, test_result.name, test_result.to_dict())
        _append_timeline(
            report,
            "tests_run",
            int((time.monotonic() - t0) * 1000),
            status=(
                "ok" if (test_result is None or test_result.passed) else "failed_open"
            ),
        )

        test_status = (
            test_result.extra.get("test_status", "skipped") if test_result else "skipped"
        )
        test_duration = test_result.duration_ms if test_result else 0

        # ---- 8. AI Self-Review (only if tests pass) ----
        if test_result is not None and test_result.passed:
            t0 = time.monotonic()
            review_result = pipeline.run_post_test(ctx)
            if review_result is not None:
                _save_validation_summary(report, review_result.name, review_result.to_dict())
                _append_timeline(
                    report,
                    "self_review",
                    int((time.monotonic() - t0) * 1000),
                    status="ok" if review_result.passed else "failed",
                )
                if not review_result.passed:
                    blocking = [
                        i.message
                        for i in review_result.issues
                        if i.severity == Severity.BLOCKER
                    ]
                    raise RuntimeError(
                        "AI self-review rejected the diff:\n- " + "\n- ".join(blocking)
                    )

        # ---- 9. PR Writer ----
        usage_agg = AgentRun.objects.filter(report=report).values_list(
            "prompt_tokens", "completion_tokens", "estimated_cost_usd"
        )
        llm_total_tokens = sum(p + c for p, c, _ in usage_agg)
        llm_total_cost = float(sum(c for _, _, c in usage_agg))

        t0 = time.monotonic()
        pr_agent = PRWriterAgent()
        pr_out = pr_agent.run(
            PRWriterInput(
                task_id=task.task_id,
                title=task.title,
                requirement=parsed.requirement,
                acceptance_criteria=parsed.acceptance_criteria,
                changed_files=[f.path for f in code_out.files],
                summary=code_out.summary,
                test_command=analysis.test_command,
                test_status=test_status,
                test_duration_ms=test_duration,
                llm_model=writer.model,
                llm_total_tokens=llm_total_tokens,
                llm_total_cost_usd=llm_total_cost,
                retries=retries,
                trace_id=trace_id,
                validation_summary=dict(report.validation_summary),
            ),
            report=report,
        )
        _append_timeline(report, "pr_drafted", int((time.monotonic() - t0) * 1000))

        # Capture the final diff BEFORE committing — once commit happens the
        # working tree matches HEAD and `git diff` returns empty.
        final_diff = git.diff(workspace_path)
        report.diff = final_diff
        report.save(update_fields=["diff"])

        # ---- 10. Dry run ----
        if task.dry_run:
            log.info("orchestrator.dry_run", task_id=task.task_id)
            report.status = ExecutionReport.Status.DRY_RUN_COMPLETE
            report.completed_at = timezone.now()
            report.save()
            task.status = Task.Status.COMPLETED
            task.save(update_fields=["status"])
            return {"status": "dry_run_complete", "trace_id": trace_id}

        # ---- 11. Commit + push + open PR ----
        t0 = time.monotonic()
        git.checkout_new_branch(workspace_path, pr_out.branch_name)
        git.commit_all(
            workspace_path,
            message=pr_out.commit_message,
            author_name="AI Development Agent",
            author_email="ai-agent@vodafone.local",
        )
        _append_timeline(report, "commit_pushed", int((time.monotonic() - t0) * 1000))

        t0 = time.monotonic()
        git.push_branch(workspace_path, pr_out.branch_name)
        _append_timeline(report, "branch_pushed", int((time.monotonic() - t0) * 1000))

        t0 = time.monotonic()
        pr_info = git.open_pull_request(
            repo_url=parsed.repository_url,
            head_branch=pr_out.branch_name,
            base_branch=parsed.base_branch,
            title=pr_out.pr_title,
            body=pr_out.pr_body,
        )
        _append_timeline(report, "pr_opened", int((time.monotonic() - t0) * 1000))

        # ---- 12. Finalize report ----
        report.pr_url = pr_info.url
        report.branch_name = pr_out.branch_name
        # report.diff was captured before commit (working tree was dirty then)
        report.llm_usage = {
            "total_tokens": llm_total_tokens,
            "total_cost_usd": round(llm_total_cost, 6),
        }
        report.status = ExecutionReport.Status.COMPLETED
        report.completed_at = timezone.now()
        report.save()

        task.status = Task.Status.COMPLETED
        task.save(update_fields=["status"])

        log.info(
            "orchestrator.completed",
            task_id=task.task_id,
            pr_url=pr_info.url,
            duration_total=sum(t["duration_ms"] for t in report.timeline),
            retries=retries,
        )
        return {"status": "completed", "pr_url": pr_info.url, "trace_id": trace_id}

    except Exception as exc:
        log.exception("orchestrator.failed", task_id=task.task_id, error=str(exc))
        _fail(task, report, str(exc))
        raise
