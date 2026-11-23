from celery import shared_task
from django.core.cache import cache
from django.utils import timezone


@shared_task(ignore_result=True)
def worker_heartbeat():
    """Only execution in a worker refreshes the shared readiness heartbeat."""
    cache.set("worker:heartbeat", timezone.now().isoformat(), timeout=180)
