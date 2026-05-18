"""GitHub Issue webhook adapter."""
from typing import Any

from .base import NormalizedTask, SkipTaskException


class GitHubIssueAdapter:
    """Maps GitHub Issues webhook payloads to a NormalizedTask.

    Triggers on 'opened', 'edited', and 'labeled' actions when the issue
    carries the gate label.

    Sample payload subset::

        {
          "action": "labeled",
          "issue": {
            "number": 42,
            "title": "...",
            "body": "Repository: ...\\nBranch: ...",
            "labels": [{"name": "ai-agent"}]
          },
          "repository": {"full_name": "owner/repo"}
        }
    """

    REQUIRED_LABEL = "ai-agent"
    ACCEPTED_ACTIONS = ("opened", "edited", "labeled", "reopened")

    @staticmethod
    def normalize(payload: dict[str, Any]) -> NormalizedTask:
        action = payload.get("action") or ""
        if action not in GitHubIssueAdapter.ACCEPTED_ACTIONS:
            raise SkipTaskException(f"Ignoring GitHub Issue action: {action or '(missing)'}")

        issue = payload.get("issue") or {}
        labels = [
            (label.get("name") or "").lower()
            for label in (issue.get("labels") or [])
            if isinstance(label, dict)
        ]
        if GitHubIssueAdapter.REQUIRED_LABEL not in labels:
            raise SkipTaskException(
                f"GitHub issue lacks required label '{GitHubIssueAdapter.REQUIRED_LABEL}'"
            )

        number = issue.get("number")
        title = issue.get("title")
        body = issue.get("body") or ""

        if not (number and title and body):
            raise ValueError(
                "Missing required GitHub Issue fields: issue.{number,title,body}"
            )

        repo = payload.get("repository") or {}
        repo_full_name = repo.get("full_name") or ""

        # If the issue body doesn't already declare a Repository, infer from the
        # event's source repo (the issue was filed against). This makes a bare
        # natural-language issue routable to the agent.
        if "Repository:" not in body and repo_full_name:
            body = (
                f"Repository: https://github.com/{repo_full_name}\n"
                f"Branch: develop\n\n{body}"
            )

        return NormalizedTask(
            task_id=f"GH-{number}",
            title=str(title),
            description=body,
            source_meta={
                "github_issue_number": number,
                "github_repo": repo_full_name,
                "github_action": action,
            },
        )
