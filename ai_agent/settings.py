"""Django settings for ai_agent project."""
import logging.config
from pathlib import Path

import environ
import structlog

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_LOG_LEVEL=(str, "INFO"),
)
environ.Env.read_env(BASE_DIR / ".env")

# ---- Core ----
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-change-me-in-production")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.tasks",
    "apps.pipeline",
]

MIDDLEWARE = [
    "apps.pipeline.trace.TraceIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ai_agent.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "ai_agent.wsgi.application"

# ---- Database ----
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="ai_agent"),
        "USER": env("POSTGRES_USER", default="ai_agent"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="ai_agent_dev_password"),
        "HOST": env("POSTGRES_HOST", default="postgres"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

# ---- REST Framework ----
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}
if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

# ---- Celery ----
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://redis:6379/2")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# ---- AI / Integrations ----
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GITHUB_TOKEN = env("GITHUB_TOKEN", default="")
WORKSPACE_ROOT = env("WORKSPACE_ROOT", default="/workspaces")

# ---- Test runner selection ----
# "subprocess" (default) runs tests in the worker container.
# "docker"    spawns an isolated Docker container per run (network=none).
#             Requires /var/run/docker.sock mounted in the worker.
TEST_RUNNER_MODE = env("TEST_RUNNER_MODE", default="subprocess")

# ---- Multi-repo PAT map ----
# Optional per-owner GitHub token overrides. Format:
#   "owner1:ghp_token1,owner2:ghp_token2"
# Falls back to GITHUB_TOKEN if not specified.
_GITHUB_TOKEN_MAP_RAW = env("GITHUB_TOKEN_MAP", default="")
GITHUB_TOKEN_MAP: dict[str, str] = {}
for _entry in _GITHUB_TOKEN_MAP_RAW.split(","):
    if ":" not in _entry:
        continue
    _owner, _token = _entry.split(":", 1)
    GITHUB_TOKEN_MAP[_owner.strip()] = _token.strip()

# ---- Security: Repository policy ----
REPOSITORY_ALLOWLIST = env.list("REPOSITORY_ALLOWLIST", default=[])

# ---- Webhook secrets (Day 4) ----
JIRA_WEBHOOK_SECRET = env("JIRA_WEBHOOK_SECRET", default="")
TRELLO_WEBHOOK_SECRET = env("TRELLO_WEBHOOK_SECRET", default="")
GITHUB_WEBHOOK_SECRET = env("GITHUB_WEBHOOK_SECRET", default="")

# ---- Logging — structlog JSON to stdout ----
LOG_LEVEL = env("DJANGO_LOG_LEVEL")

LOGGING_CONFIG = None  # disable Django's auto-config; we configure manually
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
            "foreign_pre_chain": [
                structlog.contextvars.merge_contextvars,
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso"),
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": True,
        },
        "django": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
})

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

# ---- Misc ----
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
