from celery import Celery
from celery.schedules import crontab
from app.config import settings
from app.logging_conf import setup_logging

setup_logging()

celery_app = Celery(
    "worker",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    result_expires=3600,
)

# Schedule
# Auto-schedule is now handled by DynamicScheduler in app/main.py
# celery_app.conf.beat_schedule = ...

celery_app.autodiscover_tasks(["app"])
