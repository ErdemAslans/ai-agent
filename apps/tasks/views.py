"""HTTP entry points for the AI agent.

- POST /api/tasks               direct REST submission (TaskCreateSerializer)
- POST /api/webhooks/jira       Jira webhook simulation
- POST /api/webhooks/trello     Trello webhook simulation
- POST /api/webhooks/github     GitHub Issues webhook
- GET  /api/tasks/<trace>/report  execution report
"""
import hashlib
import hmac
import uuid

import structlog
from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from .adapters.base import NormalizedTask, SkipTaskException
from .adapters.github_issue import GitHubIssueAdapter
from .adapters.jira import JiraAdapter
from .adapters.trello import TrelloAdapter
from .models import ExecutionReport, Task
from .serializers import ExecutionReportSerializer, TaskCreateSerializer

log = structlog.get_logger(__name__)


# ----------------------- helpers -----------------------------------------


def _verify_hmac(request: Request, secret: str, header_name: str,
                 prefix: str = "sha256=") -> bool:
    """Verify HMAC SHA-256 signature against request body.

    If the secret is empty, verification is bypassed (dev/simulation mode)
    but a warning is emitted so it is visible in logs.
    """
    if not secret:
        log.warning("webhook.hmac.disabled", header=header_name)
        return True
    signature = request.headers.get(header_name, "")
    if not signature:
        return False
    if signature.startswith(prefix):
        signature = signature[len(prefix):]
    expected = hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _enqueue_task(
    *,
    task_id: str,
    title: str,
    description: str,
    source: str,
    source_meta: dict | None,
    dry_run: bool,
    request: Request,
    log_prefix: str,
) -> Response:
    """Shared: persist Task + ExecutionReport, enqueue Celery, return 202."""
    trace_id = getattr(request, "trace_id", None) or str(uuid.uuid4())
    log.info(f"{log_prefix}.received", task_id=task_id, trace_id=trace_id)

    try:
        with transaction.atomic():
            task = Task.objects.create(
                task_id=task_id,
                title=title,
                description=description,
                source=source,
                source_meta=source_meta or {},
                dry_run=dry_run,
            )
            ExecutionReport.objects.create(
                task=task,
                trace_id=trace_id,
                status=ExecutionReport.Status.RUNNING,
            )
    except IntegrityError:
        existing = Task.objects.filter(task_id=task_id).first()
        log.info(
            f"{log_prefix}.duplicate",
            task_id=task_id,
            existing_status=existing.status if existing else None,
        )
        return Response(
            {
                "error": "Task already exists",
                "taskId": task_id,
                "status": existing.status if existing else "unknown",
            },
            status=status.HTTP_409_CONFLICT,
        )

    from apps.pipeline.orchestrator import orchestrate
    orchestrate.delay(str(task.id), trace_id)
    log.info(f"{log_prefix}.queued", task_id=task_id, trace_id=trace_id)

    return Response(
        {
            "taskId": task.task_id,
            "title": task.title,
            "status": task.status,
            "traceId": trace_id,
        },
        status=status.HTTP_202_ACCEPTED,
    )


def _from_webhook(
    request: Request,
    normalized: NormalizedTask,
    source: str,
    log_prefix: str,
) -> Response:
    return _enqueue_task(
        task_id=normalized.task_id,
        title=normalized.title,
        description=normalized.description,
        source=source,
        source_meta=normalized.source_meta,
        dry_run=normalized.dry_run,
        request=request,
        log_prefix=log_prefix,
    )


# ----------------------- direct REST ------------------------------------


@api_view(["POST"])
def create_task(request: Request) -> Response:
    serializer = TaskCreateSerializer(data=request.data)
    if not serializer.is_valid():
        log.warning("task.invalid", errors=serializer.errors)
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    v = serializer.validated_data
    return _enqueue_task(
        task_id=v["task_id"],
        title=v["title"],
        description=v["description"],
        source=Task.Source.API,
        source_meta={},
        dry_run=v.get("dry_run", False),
        request=request,
        log_prefix="task",
    )


# ----------------------- webhooks ----------------------------------------


@api_view(["POST"])
def jira_webhook(request: Request) -> Response:
    if not _verify_hmac(request, settings.JIRA_WEBHOOK_SECRET, "X-Hub-Signature"):
        log.warning("webhook.jira.invalid_signature")
        return Response({"error": "Invalid HMAC signature"},
                        status=status.HTTP_401_UNAUTHORIZED)

    try:
        normalized = JiraAdapter.normalize(request.data)
    except SkipTaskException as exc:
        log.info("webhook.jira.skipped", reason=str(exc))
        return Response({"skipped": str(exc)}, status=status.HTTP_200_OK)
    except (ValueError, TypeError) as exc:
        log.warning("webhook.jira.invalid", error=str(exc))
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return _from_webhook(request, normalized, Task.Source.JIRA, "webhook.jira")


@api_view(["POST"])
def trello_webhook(request: Request) -> Response:
    if not _verify_hmac(request, settings.TRELLO_WEBHOOK_SECRET, "X-Trello-Webhook"):
        log.warning("webhook.trello.invalid_signature")
        return Response({"error": "Invalid HMAC signature"},
                        status=status.HTTP_401_UNAUTHORIZED)

    try:
        normalized = TrelloAdapter.normalize(request.data)
    except SkipTaskException as exc:
        log.info("webhook.trello.skipped", reason=str(exc))
        return Response({"skipped": str(exc)}, status=status.HTTP_200_OK)
    except (ValueError, TypeError) as exc:
        log.warning("webhook.trello.invalid", error=str(exc))
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return _from_webhook(request, normalized, Task.Source.TRELLO, "webhook.trello")


@api_view(["POST"])
def github_webhook(request: Request) -> Response:
    if not _verify_hmac(request, settings.GITHUB_WEBHOOK_SECRET, "X-Hub-Signature-256"):
        log.warning("webhook.github.invalid_signature")
        return Response({"error": "Invalid HMAC signature"},
                        status=status.HTTP_401_UNAUTHORIZED)

    try:
        normalized = GitHubIssueAdapter.normalize(request.data)
    except SkipTaskException as exc:
        log.info("webhook.github.skipped", reason=str(exc))
        return Response({"skipped": str(exc)}, status=status.HTTP_200_OK)
    except (ValueError, TypeError) as exc:
        log.warning("webhook.github.invalid", error=str(exc))
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return _from_webhook(request, normalized, Task.Source.GITHUB, "webhook.github")


# ----------------------- read endpoints ---------------------------------


@api_view(["GET"])
def get_report(_request: Request, trace_id: str) -> Response:
    report = get_object_or_404(ExecutionReport, trace_id=trace_id)
    serializer = ExecutionReportSerializer(report)
    return Response(serializer.data)
