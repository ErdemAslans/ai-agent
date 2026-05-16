"""Django Admin registration — also serves as Human Approval UI (Day 4 bonus)."""
from django.contrib import admin

from .models import AgentRun, ExecutionReport, Task


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["task_id", "title", "status", "source", "dry_run", "created_at"]
    list_filter = ["status", "source", "dry_run"]
    search_fields = ["task_id", "title"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(ExecutionReport)
class ExecutionReportAdmin(admin.ModelAdmin):
    list_display = [
        "trace_id",
        "task",
        "status",
        "pr_url",
        "started_at",
        "completed_at",
    ]
    list_filter = ["status"]
    search_fields = ["trace_id", "task__task_id"]
    readonly_fields = ["id", "started_at", "completed_at"]


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
