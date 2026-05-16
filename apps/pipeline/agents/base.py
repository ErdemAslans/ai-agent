"""Common agent base — input/output, persistence helper."""
import time
from decimal import Decimal

from pydantic import BaseModel

from apps.tasks.models import AgentRun, ExecutionReport


class AgentInput(BaseModel):
    model_config = {"arbitrary_types_allowed": True}


class AgentOutput(BaseModel):
    model_config = {"arbitrary_types_allowed": True}


def record_agent_run(
    *,
    report: ExecutionReport,
    agent_name: str,
    model: str,
    started_at: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cost_usd: float = 0.0,
    input_summary: str = "",
    output_summary: str = "",
    status: str = "success",
    error: str | None = None,
) -> AgentRun:
    """Persist an AgentRun row capturing usage + duration + status."""
    duration_ms = int((time.monotonic() - started_at) * 1000)
    return AgentRun.objects.create(
        report=report,
        agent_name=agent_name,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=Decimal(str(round(cost_usd, 6))),
        duration_ms=duration_ms,
        input_summary=input_summary[:1000],
        output_summary=output_summary[:1000],
        status=status,
        error=error,
    )
