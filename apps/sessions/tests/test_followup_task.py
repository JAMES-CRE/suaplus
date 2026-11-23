import datetime as dt

import pytest
from django.test import override_settings

from apps.programmes.models import Programme
from apps.registry.models import Person
from apps.sessions.models import Attendance, Session
from apps.sessions.tasks import send_90_day_surveys


@pytest.mark.django_db
@pytest.mark.parametrize("valid_token", [True, False])
def test_followup_form_validates_tokens_without_missing_model_import(client, valid_token):
    import hashlib
    import secrets

    from django.urls import reverse
    from django.utils import timezone

    from apps.registry.models import PassportToken

    person = Person.objects.create(affiliation="staff", full_name="Follow-up participant")
    programme = Programme.objects.create(name="Follow-up form programme")
    start = timezone.now() - dt.timedelta(days=90)
    session = Session.objects.create(
        programme=programme, title="Follow-up form session", start_at=start,
        end_at=start + dt.timedelta(hours=2), mode="in_person", status="closed",
    )
    Attendance.objects.create(
        person=person, session=session, method="manual", status="completed",
        check_in_at=start, check_out_at=start + dt.timedelta(hours=2),
    )
    raw_token = secrets.token_urlsafe(32)
    if valid_token:
        PassportToken.objects.create(person=person, token_hash=hashlib.sha256(raw_token.encode()).hexdigest())
    response = client.get(reverse("followup_form", args=[raw_token]))
    assert response.status_code == 200
    if valid_token:
        assert response.context["person"].pk == person.pk
        assert response.context["attendance"].session_id == session.pk
    else:
        assert "public/followup_invalid.html" in [template.name for template in response.templates]


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
        start_at=dt.datetime(2026, 8, 15, 9, tzinfo=dt.UTC),
        end_at=dt.datetime(2026, 8, 15, 17, tzinfo=dt.UTC),
        mode="in_person",
    )
    Attendance.objects.create(
        session=session,
        person=person,
        method="manual",
        check_in_at=dt.datetime(2026, 8, 15, 9, tzinfo=dt.UTC),
        check_out_at=dt.datetime(2026, 8, 15, 17, tzinfo=dt.UTC),
        status="completed",
    )
    fixed_now = dt.datetime(2026, 11, 13, 12, tzinfo=dt.UTC)
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
