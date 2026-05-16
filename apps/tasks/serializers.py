"""DRF serializers for the tasks API."""
from rest_framework import serializers

from .models import AgentRun, ExecutionReport, Task


class TaskCreateSerializer(serializers.ModelSerializer):
    """Incoming task payload — accepts the PDF Section 2 format."""

    taskId = serializers.CharField(source="task_id", max_length=100)
    dryRun = serializers.BooleanField(source="dry_run", default=False, required=False)

    class Meta:
        model = Task
        fields = ["taskId", "title", "description", "dryRun"]


class AgentRunSerializer(serializers.ModelSerializer):
    agentName = serializers.CharField(source="agent_name")
    promptTokens = serializers.IntegerField(source="prompt_tokens")
    completionTokens = serializers.IntegerField(source="completion_tokens")
    estimatedCostUsd = serializers.DecimalField(
        source="estimated_cost_usd", max_digits=10, decimal_places=6
    )
    durationMs = serializers.IntegerField(source="duration_ms")
    startedAt = serializers.DateTimeField(source="started_at")

    class Meta:
        model = AgentRun
        fields = [
            "agentName",
            "model",
            "promptTokens",
            "completionTokens",
            "estimatedCostUsd",
            "durationMs",
            "status",
            "startedAt",
        ]


class ExecutionReportSerializer(serializers.ModelSerializer):
    traceId = serializers.CharField(source="trace_id")
    taskId = serializers.CharField(source="task.task_id", read_only=True)
    validationSummary = serializers.JSONField(source="validation_summary")
    llmUsage = serializers.JSONField(source="llm_usage")
    prUrl = serializers.URLField(source="pr_url", allow_null=True)
    branchName = serializers.CharField(source="branch_name", allow_null=True)
    startedAt = serializers.DateTimeField(source="started_at")
    completedAt = serializers.DateTimeField(source="completed_at", allow_null=True)
    agentRuns = AgentRunSerializer(source="agent_runs", many=True, read_only=True)

    class Meta:
        model = ExecutionReport
        fields = [
            "traceId",
            "taskId",
            "status",
            "timeline",
            "validationSummary",
            "llmUsage",
            "diff",
            "prUrl",
            "branchName",
            "error",
            "startedAt",
            "completedAt",
            "agentRuns",
        ]
