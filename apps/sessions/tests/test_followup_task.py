import datetime as dt

import pytest
from django.test import override_settings
from apps.programmes.models import Programme
from apps.registry.models import Person
from apps.sessions.models import Attendance, Session
from apps.sessions.tasks import send_90_day_surveys


@pytest.mark.django_db
@override_settings(SITE_URL="https://suaplus.example.org/")
def test_90_day_survey_link_uses_configured_site_url(monkeypatch):
    person = Person.objects.create(
        affiliation="staff", full_name="Follow Up Person", email="followup@example.org"
    )
    programme = Programme.objects.create(name="Follow Up Programme")
    session = Session.objects.create(
        programme=programme,
        title="Follow Up Session",
        start_at=dt.datetime(2026, 8, 15, 9, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 8, 15, 17, tzinfo=dt.timezone.utc),
        mode="in_person",
    )
    Attendance.objects.create(
        session=session,
        person=person,
        method="manual",
        check_in_at=dt.datetime(2026, 8, 15, 9, tzinfo=dt.timezone.utc),
        check_out_at=dt.datetime(2026, 8, 15, 17, tzinfo=dt.timezone.utc),
        status="completed",
    )
    fixed_now = dt.datetime(2026, 11, 13, 12, tzinfo=dt.timezone.utc)
    monkeypatch.setattr("apps.sessions.tasks.timezone.now", lambda: fixed_now)
    calls = []
    monkeypatch.setattr(
        "apps.sessions.tasks.send_notification",
        lambda **kwargs: calls.append(kwargs),
    )

    result = send_90_day_surveys()

    assert result == "Sent 1 follow-up surveys."
    assert calls[0]["context"]["survey_url"].startswith(
        "https://suaplus.example.org/followup/"
    )
