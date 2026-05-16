"""TraceIDMiddleware — assigns a trace_id to every request and binds to structlog."""
import uuid

import structlog


class TraceIDMiddleware:
    """Generates or extracts a trace_id, binds it to logging context.

    A trace_id flows through:
    1. Incoming request (or generated here)
    2. structlog context (every log line in this request includes it)
    3. Database (ExecutionReport.trace_id)
    4. Outgoing response header (X-Trace-ID for clients)
    """

    HEADER_NAME = "X-Trace-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        trace_id = request.headers.get(self.HEADER_NAME, "") or str(uuid.uuid4())
        request.trace_id = trace_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)

        response = self.get_response(request)
        response[self.HEADER_NAME] = trace_id
        return response
