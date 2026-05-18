"""Optional Langfuse instrumentation.

Provides ``observe`` and ``langfuse_context`` shims:

* If LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set in the environment
  and the SDK is installed, calls are forwarded to the real client and the
  agent/LLM spans show up at http://localhost:3000.
* Otherwise both names become no-ops so the pipeline keeps working when
  Langfuse isn't running (CI, fresh checkouts, dev without the optional
  container).
"""
import os

import structlog

log = structlog.get_logger(__name__)


def _make_noop_observe():
    def observe(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]

        def decorator(fn):
            return fn

        return decorator

    return observe


class _NoopContext:
    """Stand-in for ``langfuse.decorators.langfuse_context`` when disabled."""

    def update_current_observation(self, **_kwargs):
        return None

    def update_current_trace(self, **_kwargs):
        return None

    def get_current_trace_id(self):
        return None


ENABLED = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)

if ENABLED:
    try:
        from langfuse.decorators import langfuse_context as _real_context  # type: ignore
        from langfuse.decorators import observe as _real_observe  # type: ignore

        observe = _real_observe
        langfuse_context = _real_context
        log.info(
            "langfuse.enabled",
            host=os.environ.get("LANGFUSE_HOST", "http://langfuse:3000"),
        )
    except ImportError as exc:
        log.warning("langfuse.sdk_missing", error=str(exc))
        ENABLED = False
        observe = _make_noop_observe()
        langfuse_context = _NoopContext()
else:
    log.info("langfuse.disabled", reason="keys_not_set")
    observe = _make_noop_observe()
    langfuse_context = _NoopContext()
