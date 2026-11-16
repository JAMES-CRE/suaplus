from celery import shared_task

from apps.passport.services import expire_due_records


@shared_task
def expire_passport_records_task():
    """Run the local expiry sweep configured in Celery Beat."""
    return expire_due_records()
