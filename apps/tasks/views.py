"""API views for tasks."""
import uuid

import structlog
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from .models import ExecutionReport, Task
from .serializers import ExecutionReportSerializer, TaskCreateSerializer

log = structlog.get_logger(__name__)


@api_view(["POST"])
def create_task(request: Request) -> Response:
    """POST /api/tasks — receive a task, persist, enqueue (Day 2)."""
    serializer = TaskCreateSerializer(data=request.data)
    if not serializer.is_valid():
        log.warning("task.invalid", errors=serializer.errors)
        return Response(
            {"errors": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    trace_id = getattr(request, "trace_id", None) or str(uuid.uuid4())
    incoming_task_id = serializer.validated_data["task_id"]

    log.info("task.received", task_id=incoming_task_id, trace_id=trace_id)

    try:
        with transaction.atomic():
            task = serializer.save()
            ExecutionReport.objects.create(
                task=task,
                trace_id=trace_id,
                status=ExecutionReport.Status.RUNNING,
            )
    except IntegrityError:
        existing = Task.objects.filter(task_id=incoming_task_id).first()
        log.info(
            "task.duplicate",
            task_id=incoming_task_id,
            existing_status=existing.status if existing else None,
        )
        return Response(
            {
                "error": "Task already exists",
                "taskId": incoming_task_id,
                "status": existing.status if existing else "unknown",
            },
            status=status.HTTP_409_CONFLICT,
        )

    # Enqueue Celery pipeline
    from apps.pipeline.orchestrator import orchestrate
    orchestrate.delay(str(task.id), trace_id)

    log.info("task.queued", task_id=task.task_id, trace_id=trace_id)

    return Response(
        {
            "taskId": task.task_id,
            "title": task.title,
            "status": task.status,
            "traceId": trace_id,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
def get_report(_request: Request, trace_id: str) -> Response:
    """GET /api/tasks/<trace_id>/report — JSON execution report."""
    report = get_object_or_404(ExecutionReport, trace_id=trace_id)
    serializer = ExecutionReportSerializer(report)
    return Response(serializer.data)
