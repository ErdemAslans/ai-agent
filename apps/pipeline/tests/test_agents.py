"""Unit tests for the deterministic agents (no LLM dependency)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.pipeline.agents.pr_writer import PRWriterAgent, PRWriterInput
from apps.pipeline.agents.repo_analyzer import RepoAnalyzerAgent, RepoAnalyzerInput
from apps.pipeline.agents.task_parser import TaskParserAgent, TaskParserInput


@pytest.fixture(autouse=True)
def stub_record_agent_run():
    """Skip DB writes for these unit tests."""
    with patch(
        "apps.pipeline.agents.task_parser.record_agent_run"
    ), patch(
        "apps.pipeline.agents.repo_analyzer.record_agent_run"
    ), patch(
        "apps.pipeline.agents.pr_writer.record_agent_run"
    ):
        yield


# ---- TaskParserAgent ------------------------------------------------------


class TestTaskParserAgent:
    DESCRIPTION = (
        "Repository: https://github.com/me/my-repo\n"
        "Branch: develop\n\n"
        "Requirement:\n"
        "Add email format validation to /users/register.\n\n"
        "Acceptance Criteria:\n"
        "- Invalid email returns HTTP 400\n"
        "- Error message: Invalid email format\n"
        "- Existing valid flow preserved\n"
        "- Tests added"
    )

    def test_parses_standard_payload(self):
        result = TaskParserAgent().run(
            TaskParserInput(
                raw_task_id="T-1",
                raw_title="Add email validation",
                raw_description=self.DESCRIPTION,
            ),
            report=None,  # record_agent_run is patched
        )
        assert result.repository_url == "https://github.com/me/my-repo"
        assert result.base_branch == "develop"
        assert "email format validation" in result.requirement
        assert len(result.acceptance_criteria) == 4
        assert "Invalid email returns HTTP 400" in result.acceptance_criteria

    def test_raises_when_required_section_missing(self):
        with pytest.raises(ValueError):
            TaskParserAgent().run(
                TaskParserInput(
                    raw_task_id="T-1",
                    raw_title="x",
                    raw_description="No structured content here",
                ),
                report=None,
            )


# ---- RepoAnalyzerAgent ----------------------------------------------------


class TestRepoAnalyzerAgent:
    def test_detects_python_stack(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("flask\npytest\n")
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n"
            "# email validation needed here\n"
        )
        (tmp_path / "test_app.py").write_text(
            "def test_email():\n    assert True\n"
        )
        result = RepoAnalyzerAgent().run(
            RepoAnalyzerInput(
                workspace_path=str(tmp_path),
                requirement="Add email validation",
            ),
            report=None,
        )
        assert result.language == "Python"
        assert result.test_command == "pytest"
        assert any("app.py" in f for f in result.relevant_files)

    def test_detects_nodejs_stack(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "x"}')
        (tmp_path / "index.js").write_text("// email validation TODO\n")
        result = RepoAnalyzerAgent().run(
            RepoAnalyzerInput(
                workspace_path=str(tmp_path),
                requirement="Add email validation",
            ),
            report=None,
        )
        assert result.language == "JavaScript"
        assert result.framework == "Node.js"
        assert result.test_command == "npm test"


# ---- PRWriterAgent --------------------------------------------------------


class TestPRWriterAgent:
    def _make_input(self, **overrides):
        defaults = dict(
            task_id="TASK-1",
            title="Add validation",
            requirement="Add email format validation",
            acceptance_criteria=["a", "b"],
            changed_files=["src/app.py"],
            summary="Added regex check.",
            test_command="pytest",
            test_status="passed",
            test_duration_ms=1234,
            llm_model="gemini-2.5-flash",
            llm_total_tokens=2000,
            llm_total_cost_usd=0.0004,
            retries=0,
            trace_id="trace-abc",
            validation_summary={"file_allowlist": {"passed": True, "duration_ms": 1}},
        )
        defaults.update(overrides)
        return PRWriterInput(**defaults)

    def test_branch_name_follows_convention(self):
        out = PRWriterAgent().run(self._make_input(), report=None)
        assert out.branch_name.startswith("ai-agent/TASK-1-")
        assert out.commit_message.startswith("TASK-1")
        assert out.pr_title.startswith("TASK-1")

    def test_pr_body_includes_required_sections(self):
        out = PRWriterAgent().run(self._make_input(), report=None)
        body = out.pr_body
        for section in (
            "## Summary",
            "## Task",
            "## Acceptance Criteria",
            "## Changes",
            "## Test Result",
            "## Validation",
            "## AI Usage",
            "## Generated By",
        ):
            assert section in body, f"PR body missing '{section}'"

    def test_pr_body_shows_failed_test_marker(self):
        out = PRWriterAgent().run(self._make_input(test_status="failed"), report=None)
        assert "failed" in out.pr_body

    def test_pr_body_lists_validation_layers(self):
        summary = {
            "file_allowlist": {"passed": True, "duration_ms": 1, "issues": []},
            "secret_scan": {"passed": False, "duration_ms": 5,
                            "issues": [{"code": "X", "message": "leak"}]},
        }
        out = PRWriterAgent().run(self._make_input(validation_summary=summary), report=None)
        assert "`file_allowlist`" in out.pr_body
        assert "`secret_scan`" in out.pr_body
        assert "1 issue" in out.pr_body
