"""Persistent data models for tasks, execution reports, and agent runs."""
import uuid

from django.db import models


class Task(models.Model):
    """A development task received from any source (API, CLI, webhook)."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        API = "api", "REST API"
        CLI = "cli", "Command Line"
        JIRA = "jira", "Jira Webhook"
        TRELLO = "trello", "Trello Webhook"
        GITHUB = "github", "GitHub Issue"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_id = models.CharField(max_length=100, unique=True, db_index=True)
    title = models.CharField(max_length=500)
    description = models.TextField()

    # Parsed by TaskParserAgent (Day 2). Nullable on creation.
    repository_url = models.URLField(null=True, blank=True)
    base_branch = models.CharField(max_length=200, null=True, blank=True)
    requirement = models.TextField(null=True, blank=True)
    acceptance_criteria = models.JSONField(default=list)

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.API)
    source_meta = models.JSONField(default=dict)
    dry_run = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.task_id}: {self.title[:50]}"


class ExecutionReport(models.Model):
    """Audit trail of one full pipeline execution."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        DRY_RUN_COMPLETE = "dry_run_complete", "Dry-run Complete"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.PROTECT, related_name="reports")
    trace_id = models.CharField(max_length=64, unique=True, db_index=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    timeline = models.JSONField(default=list)
    validation_summary = models.JSONField(default=dict)
    llm_usage = models.JSONField(default=dict)

    diff = models.TextField(null=True, blank=True)
    pr_url = models.URLField(null=True, blank=True)
    branch_name = models.CharField(max_length=200, null=True, blank=True)

    error = models.TextField(null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.trace_id} ({self.status})"


class AgentRun(models.Model):
    """One execution of a single agent (TaskParser, CodeWriter, etc.)."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        ExecutionReport, on_delete=models.CASCADE, related_name="agent_runs"
    )

    agent_name = models.CharField(max_length=50)
    model = models.CharField(max_length=50, blank=True)

    prompt_tokens = models.IntegerField(default=0)
    completion_tokens = models.IntegerField(default=0)
    estimated_cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    duration_ms = models.IntegerField(default=0)

    input_summary = models.TextField(blank=True)
    output_summary = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=Status.choices)
    error = models.TextField(null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["started_at"]
        indexes = [
            models.Index(fields=["agent_name", "status"]),
        ]

    def __str__(self):
        return f"{self.agent_name} ({self.status})"
