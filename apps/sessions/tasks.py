import hashlib
import secrets
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.notifications.services import send_notification
from apps.registry.models import PassportToken
from apps.sessions.models import Attendance


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
        site_url = settings.SITE_URL.rstrip("/")
        survey_url = f"{site_url}/followup/{raw}/"

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
