"""Django Admin — also exposes the Human Approval flow for dry-run PRs."""
from pathlib import Path

import structlog
from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .models import AgentRun, ExecutionReport, Task

log = structlog.get_logger(__name__)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["task_id", "title", "status", "source", "dry_run", "created_at"]
    list_filter = ["status", "source", "dry_run"]
    search_fields = ["task_id", "title"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.action(description="Approve & open PR (uses captured diff)")
def approve_and_open_pr(modeladmin, request, queryset):
    """Open PRs for ExecutionReports that completed in dry-run mode."""
    from apps.pipeline.providers.git.github import GitHubProvider
    from apps.pipeline.agents.pr_writer import PRWriterAgent, PRWriterInput

    git = GitHubProvider(
        token=settings.GITHUB_TOKEN,
        token_map=settings.GITHUB_TOKEN_MAP,
    )
    pr_agent = PRWriterAgent()

    eligible = queryset.filter(status=ExecutionReport.Status.DRY_RUN_COMPLETE)
    skipped = queryset.exclude(status=ExecutionReport.Status.DRY_RUN_COMPLETE).count()
    if skipped:
        messages.warning(
            request,
            f"{skipped} report(s) skipped — only DRY_RUN_COMPLETE can be approved.",
        )

    opened = 0
    for report in eligible:
        if not report.workspace_path:
            messages.error(request, f"{report.trace_id}: missing workspace_path; cannot push.")
            continue
        workspace = Path(report.workspace_path)
        if not workspace.exists():
            messages.error(
                request,
                f"{report.trace_id}: workspace expired ({workspace}). Re-run with dryRun=false.",
            )
            continue

        task = report.task
        # Rebuild PR title/body from the persisted task + summary metadata.
        # We don't re-invoke the LLM here — the dry-run already produced the diff.
        try:
            pr_input = PRWriterInput(
                task_id=task.task_id,
                title=task.title,
                requirement=task.requirement or "",
                acceptance_criteria=task.acceptance_criteria or [],
                changed_files=[],  # captured in diff; explicit list not required for approval
                summary=f"Approved dry-run for {task.task_id}",
                test_command="",
                test_status="approved",
                test_duration_ms=0,
                llm_model="manual-approval",
                llm_total_tokens=report.llm_usage.get("total_tokens", 0),
                llm_total_cost_usd=report.llm_usage.get("total_cost_usd", 0.0),
                retries=0,
                trace_id=report.trace_id,
                validation_summary=dict(report.validation_summary or {}),
            )
            pr_out = pr_agent.run(pr_input, report=report)

            git.checkout_new_branch(workspace, pr_out.branch_name)
            git.commit_all(
                workspace,
                message=pr_out.commit_message,
                author_name="AI Development Agent",
                author_email="ai-agent@vodafone.local",
            )
            git.push_branch(workspace, pr_out.branch_name)
            pr_info = git.open_pull_request(
                repo_url=task.repository_url,
                head_branch=pr_out.branch_name,
                base_branch=task.base_branch,
                title=pr_out.pr_title,
                body=pr_out.pr_body,
            )
            report.pr_url = pr_info.url
            report.branch_name = pr_out.branch_name
            report.status = ExecutionReport.Status.COMPLETED
            report.save()
            task.status = Task.Status.COMPLETED
            task.save(update_fields=["status"])
            opened += 1
        except Exception as exc:
            log.exception("admin.approval.failed", trace_id=report.trace_id, error=str(exc))
            messages.error(request, f"{report.trace_id}: {exc}")

    if opened:
        messages.success(request, f"Opened {opened} PR(s) from dry-run approvals.")


@admin.register(ExecutionReport)
class ExecutionReportAdmin(admin.ModelAdmin):
    list_display = [
        "trace_id",
        "task",
        "status",
        "pr_link",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status"]
    search_fields = ["trace_id", "task__task_id"]
    readonly_fields = ["id", "started_at", "completed_at"]
    actions = [approve_and_open_pr]

    def pr_link(self, obj):
        if obj.pr_url:
            return format_html('<a href="{}" target="_blank">PR</a>', obj.pr_url)
        return "—"
    pr_link.short_description = "PR"


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = [
        "agent_name",
        "model",
        "status",
        "estimated_cost_usd",
        "duration_ms",
        "started_at",
    ]
    list_filter = ["agent_name", "status", "model"]
    search_fields = ["report__trace_id", "agent_name"]
