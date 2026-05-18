"""Common types for webhook payload adapters."""
from dataclasses import dataclass, field


class SkipTaskException(Exception):
    """Raised when a webhook should be silently ignored.

    Examples: card not in the ai-agent list, issue lacks the gate label,
    irrelevant event type. The endpoint returns HTTP 200 so the source
    system stops retrying.
    """


@dataclass
class NormalizedTask:
    """Adapter output shape — what all sources normalize down to."""

    task_id: str
    title: str
    description: str
    dry_run: bool = False
    source_meta: dict = field(default_factory=dict)
