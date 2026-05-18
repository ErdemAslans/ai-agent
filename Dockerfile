# ===== Stage 1: Builder =====
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./

RUN pip install --upgrade pip && \
    pip install --prefix=/install \
    "Django>=5.0,<6.0" \
    "djangorestframework>=3.14,<4.0" \
    "django-environ>=0.11" \
    "celery[redis]>=5.3,<6.0" \
    "redis>=5.0,<6.0" \
    "psycopg2-binary>=2.9" \
    "google-genai>=0.3" \
    "GitPython>=3.1" \
    "PyGithub>=2.1" \
    "docker>=7.0" \
    "structlog>=24.0" \
    "pydantic>=2.5" \
    "gunicorn>=22.0,<23.0" \
    "langfuse>=2.50,<3.0" \
    "pytest>=7.4" \
    "pytest-django>=4.7"

# ===== Stage 2: Runtime =====
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --shell /bin/bash --home-dir /home/app --create-home app \
    && mkdir -p /workspaces && chown app:app /workspaces

COPY --from=builder /install /usr/local

COPY --chown=app:app . .

USER app

# pip --user installs land in ~/.local; expose binaries on PATH for TestRunner
ENV PATH="/home/app/.local/bin:${PATH}"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/ || exit 1

CMD ["gunicorn", "ai_agent.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
