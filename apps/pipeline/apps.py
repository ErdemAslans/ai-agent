from django.apps import AppConfig


class PipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.pipeline"

    def ready(self):
        # Register Celery tasks defined outside tasks.py
        from . import orchestrator  # noqa: F401
