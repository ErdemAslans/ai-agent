"""Jira webhook adapter (simulation-friendly, real-format compatible)."""
from typing import Any
from urllib.parse import unquote

from .base import NormalizedTask, SkipTaskException


def _decode(value: Any) -> str:
    """URL-decode a field if it looks encoded; pass through otherwise.

    Jira automation rules typically don't JSON-escape control characters
    or quotes inside `{{issue.description}}` smart values, which breaks
    strict JSON parsing on the receiver. The recommended workaround is
    to pipe the value through `{{...urlEncode}}` in the body template
    and unquote it here — a no-op for plain fields, a rescue for rich
    descriptions with newlines and embedded quotes.
    """
    if value is None:
        return ""
    text = str(value)
    if "%" in text:
        return unquote(text)
    return text


class JiraAdapter:
    """Maps Atlassian Jira issue-event payloads to a NormalizedTask.

    Sample payload subset::

        {
          "webhookEvent": "jira:issue_created",
          "issue": {
            "key": "TASK-200",
            "fields": {
              "summary": "...",
              "description": "Repository%3A%20...%0ABranch%3A%20...",
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
        title = _decode(fields.get("summary"))
        description = _decode(fields.get("description"))

        if not (task_id and title and description):
            raise ValueError(
                "Missing required Jira fields: issue.key, fields.summary, fields.description"
            )

        return NormalizedTask(
            task_id=str(task_id),
            title=title,
            description=description,
            source_meta={
                "webhook_event": event,
                "jira_issue_key": task_id,
                "priority": (fields.get("priority") or {}).get("name"),
            },
        )
