"""CLI entry point: ``python manage.py run_task --file examples/task.json``."""
import json
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from apps.tasks.models import ExecutionReport, Task


class Command(BaseCommand):
    help = "Submit a task to the AI development agent pipeline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--file", "-f", type=str, required=True,
            help="Path to a JSON file with taskId, title, description.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Run validation + diff capture but do not open a PR.",
        )
        parser.add_argument(
            "--sync", action="store_true",
            help="Execute synchronously in this process (skip Celery enqueue).",
        )

    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc

        for field in ("taskId", "title", "description"):
            if field not in data:
                raise CommandError(f"Missing required field: {field}")

        dry_run = options["dry_run"] or data.get("dryRun", False)
        trace_id = str(uuid.uuid4())

        try:
            with transaction.atomic():
                task = Task.objects.create(
                    task_id=data["taskId"],
                    title=data["title"],
                    description=data["description"],
                    source=Task.Source.CLI,
                    source_meta={"file": str(path.resolve())},
                    dry_run=dry_run,
                )
                ExecutionReport.objects.create(
                    task=task,
                    trace_id=trace_id,
                    status=ExecutionReport.Status.RUNNING,
                )
        except IntegrityError:
            raise CommandError(
                f"Task with id '{data['taskId']}' already exists. "
                "Delete it first or pick a new taskId."
            )

        # Imported lazily so this command works even before Celery is wired.
        from apps.pipeline.orchestrator import orchestrate

        if options["sync"]:
            self.stdout.write(f"Running task {data['taskId']} synchronously...")
            result = orchestrate.apply(args=[str(task.id), trace_id]).get()
            self.stdout.write(self.style.SUCCESS(
                f"Result:\n{json.dumps(result, indent=2)}"
            ))
        else:
            orchestrate.delay(str(task.id), trace_id)
            self.stdout.write(self.style.SUCCESS(
                f"Task '{data['taskId']}' enqueued."
            ))
            self.stdout.write(f"  Trace ID: {trace_id}")
            self.stdout.write(
                f"  Poll:     curl http://localhost:8000/api/tasks/{trace_id}/report"
            )
