from celery import Celery

from app.config import settings

# Must never import the FastAPI app (ADR-13).
celery_app = Celery(
    "worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    result_extended=True,
    result_expires=settings.celery_result_expires_seconds,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
