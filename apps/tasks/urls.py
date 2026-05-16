"""URL patterns for the tasks app."""
from django.urls import path

from . import views

urlpatterns = [
    path("tasks", views.create_task, name="create-task"),
    path("tasks/<str:trace_id>/report", views.get_report, name="get-report"),
]
