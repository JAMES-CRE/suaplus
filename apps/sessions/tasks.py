from celery import shared_task
from django.utils import timezone
from datetime import timedelta
import secrets
import hashlib

from apps.sessions.models import Attendance
from apps.notifications.services import send_notification
from apps.registry.models import PassportToken


@shared_task
def send_90_day_surveys():
    """Find sessions that happened exactly 90 days ago and email attendees
    with a personalised follow-up survey link (SUR-02).
    """
    target_date = timezone.now().date() - timedelta(days=90)

    attendances = Attendance.objects.filter(
        status="completed",
        session__start_at__date=target_date,
    ).select_related("person", "session")

    count = 0
    for att in attendances:
        # Generate a fresh token for the follow-up link
        raw = secrets.token_urlsafe(32)
        PassportToken.objects.create(
            person=att.person,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        )
        survey_url = f"http://127.0.0.1:8000/followup/{raw}/"

        send_notification(
            person=att.person,
            template_key="followup_survey",
            context={
                "name": att.person.full_name,
                "session_title": att.session.title,
                "survey_url": survey_url,
            },
        )
        count += 1

    return f"Sent {count} follow-up surveys."