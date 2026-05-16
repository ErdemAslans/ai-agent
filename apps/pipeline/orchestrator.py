"""Orchestrator Celery task — wires agents + providers + workspace + git.

Pipeline:
  1. Parse task description
  2. Validate repository_url against allowlist
  3. Create workspace + clone repo
  4. Analyze repo (language, test cmd, relevant files)
  5. Run CodeWriter (LLM)
  6. Run tests (Day 3: real, Day 2: skipped)
  7. Run TestFixer if failed (Day 3)
  8. Generate PR title/body
  9. Push branch + open PR (or dry-run)
 10. Update ExecutionReport
"""
import fnmatch
import time
from datetime import datetime
from pathlib import Path

import structlog
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.tasks.models import ExecutionReport, Task

from .agents.code_writer import CodeWriterAgent, CodeWriterInput
from .agents.pr_writer import PRWriterAgent, PRWriterInput
from .agents.repo_analyzer import RepoAnalyzerAgent, RepoAnalyzerInput
from .agents.task_parser import TaskParserAgent, TaskParserInput
from .agents.test_fixer import TestFixerAgent
from .providers.git.github import GitHubProvider
from .providers.llm.gemini import GeminiProvider
from .workspace import WorkspaceManager

log = structlog.get_logger(__name__)


def _check_repo_allowed(repo_url: str) -> None:
    """Repository allowlist gate (PDF Section 6.1)."""
    allowlist = settings.REPOSITORY_ALLOWLIST
    if not allowlist:
        log.warning("repo.allowlist.empty", repo_url=repo_url, note="dev mode — accepting all")
        return
    for pattern in allowlist:
        if fnmatch.fnmatch(repo_url, pattern) or fnmatch.fnmatch(repo_url, f"*{pattern}*"):
            return
    raise PermissionError(
        f"Repository '{repo_url}' is not in REPOSITORY_ALLOWLIST"
    )


def _build_file_tree(workspace: Path, max_entries: int = 200) -> list[str]:
    """Flat list of file paths (relative), capped."""
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


def _append_timeline(report: ExecutionReport, step: str, duration_ms: int, status: str = "ok"):
    report.refresh_from_db()
    report.timeline.append(
        {
            "step": step,
            "at": datetime.utcnow().isoformat() + "Z",
            "duration_ms": duration_ms,
            "status": status,
        }
    )
    report.save(update_fields=["timeline"])


@shared_task(bind=True, name="apps.pipeline.orchestrator.orchestrate")
def orchestrate(self, task_uuid: str, trace_id: str) -> dict:
    """Main pipeline entry. Called by POST /api/tasks view."""
    task = Task.objects.get(id=task_uuid)
    report = ExecutionReport.objects.get(trace_id=trace_id)

    log.info("orchestrator.start", task_id=task.task_id, trace_id=trace_id)
    task.status = Task.Status.RUNNING
    task.save(update_fields=["status"])
    report.status = ExecutionReport.Status.RUNNING
    report.save(update_fields=["status"])

    # Pre-init providers (fail fast if creds missing)
    try:
        llm = GeminiProvider(api_key=settings.GEMINI_API_KEY)
        git = GitHubProvider(token=settings.GITHUB_TOKEN)
        workspace_mgr = WorkspaceManager()
    except ValueError as exc:
        log.error("orchestrator.config_missing", error=str(exc))
        _fail(task, report, f"Configuration error: {exc}")
        raise

    workspace_path: Path | None = None
    llm_total_tokens = 0
    llm_total_cost = 0.0

    try:
        # ---- 1. Parse task ----
        t0 = time.monotonic()
        parser = TaskParserAgent()
        parsed = parser.run(
            TaskParserInput(
                raw_task_id=task.task_id,
                raw_title=task.title,
                raw_description=task.description,
            ),
            report=report,
        )
        _append_timeline(report, "task_parsed", int((time.monotonic() - t0) * 1000))

        # Save parsed fields back to Task
        task.repository_url = parsed.repository_url
        task.base_branch = parsed.base_branch
        task.requirement = parsed.requirement
        task.acceptance_criteria = parsed.acceptance_criteria
        task.save()

        # ---- 2. Repo allowlist ----
        _check_repo_allowed(parsed.repository_url)

        # ---- 3. Workspace + clone ----
        t0 = time.monotonic()
        workspace_path = workspace_mgr.create(task.task_id, trace_id)
        _append_timeline(report, "workspace_created", int((time.monotonic() - t0) * 1000))

        t0 = time.monotonic()
        git.clone(parsed.repository_url, parsed.base_branch, workspace_path)
        _append_timeline(report, "clone_completed", int((time.monotonic() - t0) * 1000))

        # ---- 4. Analyze repo ----
        t0 = time.monotonic()
        analyzer = RepoAnalyzerAgent()
        analysis = analyzer.run(
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
        file_tree = _build_file_tree(workspace_path)
        code_out = writer.run(
            CodeWriterInput(
                workspace_path=str(workspace_path),
                requirement=parsed.requirement,
                acceptance_criteria=parsed.acceptance_criteria,
                relevant_files=analysis.relevant_files,
                file_tree=file_tree,
                language=analysis.language,
                framework=analysis.framework,
            ),
            report=report,
        )
        _append_timeline(report, "code_changed", int((time.monotonic() - t0) * 1000))

        # ---- 6. Tests (Day 2: skipped — Day 3 adds real test runner) ----
        test_status = "skipped"
        test_duration = 0

        # ---- 7. TestFixer (Day 2: stub) ----
        if test_status == "failed":
            fixer = TestFixerAgent(llm=llm)
            # Stub for now; Day 3 wires retry loop
            _ = fixer

        # ---- 8. PRWriter ----
        # Aggregate LLM usage from this task's agent runs
        from apps.tasks.models import AgentRun
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
                retries=0,
                trace_id=trace_id,
            ),
            report=report,
        )
        _append_timeline(report, "pr_drafted", int((time.monotonic() - t0) * 1000))

        # ---- 9. Dry-run check ----
        if task.dry_run:
            log.info("orchestrator.dry_run", task_id=task.task_id)
            report.diff = git.diff(workspace_path)
            report.status = ExecutionReport.Status.DRY_RUN_COMPLETE
            report.completed_at = timezone.now()
            report.save()
            task.status = Task.Status.COMPLETED
            task.save(update_fields=["status"])
            return {"status": "dry_run_complete", "trace_id": trace_id}

        # ---- 10. Commit, push, open PR ----
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

        report.pr_url = pr_info.url
        report.branch_name = pr_out.branch_name
        report.diff = git.diff(workspace_path)
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
        )
        return {"status": "completed", "pr_url": pr_info.url, "trace_id": trace_id}

    except Exception as exc:
        log.exception("orchestrator.failed", task_id=task.task_id, error=str(exc))
        _fail(task, report, str(exc))
        raise


def _fail(task: Task, report: ExecutionReport, error: str) -> None:
    report.error = error
    report.status = ExecutionReport.Status.FAILED
    report.completed_at = timezone.now()
    report.save()
    task.status = Task.Status.FAILED
    task.save(update_fields=["status"])
