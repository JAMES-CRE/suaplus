from celery import shared_task
from apps.notifications.services import send_notification

@shared_task
def send_notification_task(person_id, template_key, context):
    """Background task to send an email without blocking the request."""
    from apps.registry.models import Person
    person = Person.objects.filter(id=person_id).first()
    if person:
        send_notification(person, template_key, context)