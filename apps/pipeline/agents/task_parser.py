"""TaskParserAgent — hybrid (regex first, LLM fallback).

Day 2: regex only. Day 3: add LLM fallback for malformed descriptions.
"""
import re
import time

import structlog

from apps.pipeline.observability import langfuse_context, observe
from apps.tasks.models import ExecutionReport

from .base import AgentInput, AgentOutput, record_agent_run

log = structlog.get_logger(__name__)


class TaskParserInput(AgentInput):
    raw_task_id: str
    raw_title: str
    raw_description: str


class TaskParserOutput(AgentOutput):
    task_id: str
    repository_url: str
    base_branch: str
    requirement: str
    acceptance_criteria: list[str]


class TaskParserAgent:
    name = "task_parser"

    def __init__(self, llm=None):
        self.llm = llm  # reserved for Day 3 LLM fallback

    @observe(name="agent.task_parser", capture_input=False, capture_output=False)
    def run(self, input: TaskParserInput, report: ExecutionReport) -> TaskParserOutput:
        langfuse_context.update_current_observation(
            input={"raw_task_id": input.raw_task_id, "title": input.raw_title},
        )
        started = time.monotonic()
        parsed = self._regex_parse(input.raw_description)
        if not parsed:
            record_agent_run(
                report=report,
                agent_name=self.name,
                model="regex",
                started_at=started,
                status="failed",
                error="Regex could not parse task description",
                input_summary=input.raw_description[:500],
            )
            raise ValueError(
                "Could not parse task description with regex. "
                "Expected sections: Repository, Branch, Requirement, Acceptance Criteria."
            )

        output = TaskParserOutput(task_id=input.raw_task_id, **parsed)
        record_agent_run(
            report=report,
            agent_name=self.name,
            model="regex",
            started_at=started,
            input_summary=input.raw_description[:500],
            output_summary=output.model_dump_json()[:500],
        )
        log.info(
            "task_parser.parsed",
            task_id=output.task_id,
            repo=output.repository_url,
            branch=output.base_branch,
            ac_count=len(output.acceptance_criteria),
        )
        langfuse_context.update_current_observation(
            output={
                "repository_url": output.repository_url,
                "base_branch": output.base_branch,
                "acceptance_criteria_count": len(output.acceptance_criteria),
            },
        )
        return output

    # Capture a URL even when the upstream system (e.g. Jira) wraps it in
    # smart-link wiki markup like [https://...|https://...|smart-link].
    # Stops at the first whitespace, pipe, or closing bracket.
    _URL_PATTERN = r"\[?(https?://[^\s|\]]+)"

    def _regex_parse(self, desc: str) -> dict | None:
        repo_match = re.search(rf"Repository:\s*{self._URL_PATTERN}", desc)
        branch_match = re.search(r"Branch:\s*(\S+)", desc)
        req_match = re.search(
            r"Requirement:\s*\n?(.+?)(?=\n\s*Acceptance Criteria:|\Z)",
            desc,
            re.DOTALL,
        )
        ac_match = re.search(
            r"Acceptance Criteria:\s*\n?(.+?)$",
            desc,
            re.DOTALL,
        )

        if not (repo_match and branch_match and req_match and ac_match):
            return None

        ac_lines = [
            line.lstrip("-*• ").strip()
            for line in ac_match.group(1).strip().splitlines()
            if line.strip().lstrip("-*• ").strip()
        ]

        return {
            "repository_url": repo_match.group(1).strip().rstrip("/"),
            "base_branch": branch_match.group(1).strip(),
            "requirement": req_match.group(1).strip(),
            "acceptance_criteria": ac_lines,
        }
