"""Unit tests for webhook payload adapters."""
import pytest

from apps.tasks.adapters.base import SkipTaskException
from apps.tasks.adapters.github_issue import GitHubIssueAdapter
from apps.tasks.adapters.jira import JiraAdapter
from apps.tasks.adapters.trello import TrelloAdapter


# ---- Jira -----------------------------------------------------------------


def _jira_payload(**overrides):
    payload = {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "key": "PROJ-1",
            "fields": {
                "summary": "Add validation",
                "description": "Repository: https://github.com/x/y\nBranch: develop",
                "labels": ["ai-agent", "backend"],
                "priority": {"name": "High"},
            },
        },
    }
    if "labels" in overrides:
        payload["issue"]["fields"]["labels"] = overrides.pop("labels")
    if "event" in overrides:
        payload["webhookEvent"] = overrides.pop("event")
    payload.update(overrides)
    return payload


class TestJiraAdapter:
    def test_normalizes_valid_payload(self):
        result = JiraAdapter.normalize(_jira_payload())
        assert result.task_id == "PROJ-1"
        assert result.title == "Add validation"
        assert "Repository:" in result.description
        assert result.source_meta["jira_issue_key"] == "PROJ-1"
        assert result.source_meta["priority"] == "High"

    def test_skips_when_label_missing(self):
        with pytest.raises(SkipTaskException, match="ai-agent"):
            JiraAdapter.normalize(_jira_payload(labels=["bug"]))

    def test_skips_irrelevant_event(self):
        with pytest.raises(SkipTaskException):
            JiraAdapter.normalize(_jira_payload(event="comment:created"))

    def test_rejects_missing_required_field(self):
        payload = _jira_payload()
        payload["issue"]["fields"]["summary"] = ""
        with pytest.raises(ValueError):
            JiraAdapter.normalize(payload)


# ---- Trello ---------------------------------------------------------------


def _trello_payload(list_name="ai-agent"):
    return {
        "action": {
            "type": "createCard",
            "data": {
                "card": {
                    "id": "abc123",
                    "name": "Add validation",
                    "desc": "Repository: https://github.com/x/y\nBranch: develop",
                },
                "list": {"name": list_name},
                "board": {"name": "Dev"},
            },
        },
    }


class TestTrelloAdapter:
    def test_normalizes_valid_payload(self):
        result = TrelloAdapter.normalize(_trello_payload())
        assert result.task_id == "TRELLO-abc123"
        assert result.title == "Add validation"
        assert result.source_meta["trello_list"] == "ai-agent"

    def test_skips_when_list_mismatch(self):
        with pytest.raises(SkipTaskException, match="ai-agent"):
            TrelloAdapter.normalize(_trello_payload(list_name="Backlog"))

    def test_skips_non_card_event(self):
        payload = _trello_payload()
        payload["action"]["type"] = "updateList"
        with pytest.raises(SkipTaskException):
            TrelloAdapter.normalize(payload)


# ---- GitHub Issue ---------------------------------------------------------


def _github_payload(label="ai-agent", action="labeled"):
    return {
        "action": action,
        "issue": {
            "number": 7,
            "title": "Add validation",
            "body": "Repository: https://github.com/x/y\nBranch: develop\n\nRequirement: ...",
            "labels": [{"name": label}],
        },
        "repository": {"full_name": "x/y"},
    }


class TestGitHubIssueAdapter:
    def test_normalizes_valid_payload(self):
        result = GitHubIssueAdapter.normalize(_github_payload())
        assert result.task_id == "GH-7"
        assert result.title == "Add validation"
        assert "github.com/x/y" in result.description

    def test_injects_repository_when_body_misses_it(self):
        payload = _github_payload()
        payload["issue"]["body"] = "Plain natural-language requirement."
        result = GitHubIssueAdapter.normalize(payload)
        assert "Repository: https://github.com/x/y" in result.description
        assert "Branch: develop" in result.description

    def test_skips_when_label_missing(self):
        with pytest.raises(SkipTaskException):
            GitHubIssueAdapter.normalize(_github_payload(label="bug"))

    def test_skips_unsupported_action(self):
        with pytest.raises(SkipTaskException):
            GitHubIssueAdapter.normalize(_github_payload(action="closed"))
