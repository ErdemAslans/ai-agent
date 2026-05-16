"""TestFixerAgent — feeds failing test output back to LLM for repair.

Day 2: stub. Day 3: real implementation with retry loop.
"""
import time

import structlog

from apps.tasks.models import ExecutionReport

from .base import AgentInput, AgentOutput, record_agent_run

log = structlog.get_logger(__name__)


class TestFixerInput(AgentInput):
    workspace_path: str
    test_output: str
    last_changed_files: list[str]


class TestFixerOutput(AgentOutput):
    changed_files: list[str]
    summary: str


class TestFixerAgent:
    """Day 2 stub — implementation in Day 3."""

    name = "test_fixer"

    def __init__(self, llm=None, model: str = "gemini-2.5-pro"):
        self.llm = llm
        self.model = model

    def run(self, input: TestFixerInput, report: ExecutionReport) -> TestFixerOutput:
        started = time.monotonic()
        log.info("test_fixer.stub", note="Day 2 — not implemented yet")
        output = TestFixerOutput(
            changed_files=[],
            summary="TestFixer is a Day 2 stub. Implemented in Day 3.",
        )
        record_agent_run(
            report=report,
            agent_name=self.name,
            model="stub",
            started_at=started,
            input_summary=input.test_output[:300],
            output_summary=output.summary,
        )
        return output
