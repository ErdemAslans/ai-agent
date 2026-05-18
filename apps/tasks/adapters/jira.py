"""Jira webhook adapter (simulation-friendly, real-format compatible)."""
from typing import Any

from .base import NormalizedTask, SkipTaskException


class JiraAdapter:
    """Maps Atlassian Jira issue-event payloads to a NormalizedTask.

    Sample payload subset::

        {
          "webhookEvent": "jira:issue_created",
          "issue": {
            "key": "TASK-200",
            "fields": {
              "summary": "...",
              "description": "Repository: ...\\nBranch: ...",
              "labels": ["ai-agent"]
            }
          }
        }
    """

    REQUIRED_LABEL = "ai-agent"
    ACCEPTED_EVENTS = ("jira:issue_created", "jira:issue_updated")

    @staticmethod
    def normalize(payload: dict[str, Any]) -> NormalizedTask:
        event = payload.get("webhookEvent", "")
        if event not in JiraAdapter.ACCEPTED_EVENTS:
            raise SkipTaskException(f"Ignoring Jira event: {event or '(missing)'}")

        issue = payload.get("issue") or {}
        fields = issue.get("fields") or {}

        labels = [str(label).lower() for label in (fields.get("labels") or [])]
        if JiraAdapter.REQUIRED_LABEL not in labels:
            raise SkipTaskException(
                f"Jira issue is missing required label '{JiraAdapter.REQUIRED_LABEL}'"
            )

        task_id = issue.get("key")
        title = fields.get("summary")
        description = fields.get("description")

        if not (task_id and title and description):
            raise ValueError(
                "Missing required Jira fields: issue.key, fields.summary, fields.description"
            )

        return NormalizedTask(
            task_id=str(task_id),
            title=str(title),
            description=str(description),
            source_meta={
                "webhook_event": event,
                "jira_issue_key": task_id,
                "priority": (fields.get("priority") or {}).get("name"),
            },
        )
