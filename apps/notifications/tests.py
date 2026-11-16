from django.test import TestCase

# Create your tests here.
import pytest
from unittest.mock import Mock, patch
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings

from apps.accounts.models import RoleAssignment, UserAccount
from apps.notifications.models import Notification
from apps.notifications.services import (
    send_passport_link_to_participant,
    send_passport_link_to_partner_coordinators,
    send_sms,
)
from apps.registry.models import Consent, Organisation, Person


@pytest.mark.django_db
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="Sua+ <noreply@example.org>",
)
def test_minor_passport_link_only_goes_to_partner_coordinator():
    organisation = Organisation.objects.create(
        name="Example School", type="senior_high_school"
    )
    minor = Person.objects.create(
        affiliation="external",
        full_name="Student Example",
        email="student@example.org",
        age_band="under_18",
        organisation=organisation,
    )
    User = get_user_model()
    coordinator_user = User.objects.create_user(
        username="coordinator@example.org",
        email="coordinator@example.org",
        password="test-password",
    )
    coordinator = UserAccount.objects.create(user=coordinator_user)
    RoleAssignment.objects.create(
        user_account=coordinator,
        role="partner_coordinator",
        scope_type="organisation",
        scope_id=organisation.id,
    )

    sent = send_passport_link_to_partner_coordinators(
        minor, "Example Workshop", "https://suaplus.example/p/opaque-token/"
    )

    assert sent == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["coordinator@example.org"]
    assert "student@example.org" not in mail.outbox[0].to
    assert Notification.objects.get(person=minor).status == "sent"


@pytest.mark.django_db
@override_settings(SMS_PROVIDER_URL="", SMS_PROVIDER_KEY="")
def test_sms_without_provider_is_logged_as_failed():
    person = Person.objects.create(
        affiliation="staff", full_name="SMS Recipient", phone="+233240000000"
    )

    notification = send_sms(person, "Sua+ update")

    assert notification.status == "failed"
    assert notification.attempts == 1
    assert notification.last_error == "SMS provider is not configured."
    assert Notification.objects.get(person=person).sent_at is None


@pytest.mark.django_db
@override_settings(
    SMS_PROVIDER_URL="https://sms.example.test/messages",
    SMS_PROVIDER_KEY="test-key",
)
def test_sms_provider_success_records_delivery_and_provider_reference():
    person = Person.objects.create(
        affiliation="staff", full_name="SMS Recipient", phone="+233240000000"
    )
    response = Mock()
    response.json.return_value = {"message_id": "sms-123"}

    with patch("requests.post", return_value=response) as post:
        notification = send_sms(person, "Sua+ update")

    post.assert_called_once_with(
        "https://sms.example.test/messages",
        headers={"Authorization": "Bearer test-key"},
        json={"to": "+233240000000", "message": "Sua+ update"},
        timeout=10,
    )
    assert notification.status == "sent"
    assert notification.provider_ref == "sms-123"
    assert notification.sent_at is not None


@pytest.mark.django_db
@override_settings(SMS_PROVIDER_URL="", SMS_PROVIDER_KEY="")
def test_passport_sms_is_attempted_only_with_active_sms_consent():
    person = Person.objects.create(
        affiliation="staff",
        full_name="Consent Recipient",
        email="consent@example.org",
        phone="+233240000000",
    )
    with patch("apps.notifications.services.send_notification") as email, patch(
        "apps.notifications.services.send_sms"
    ) as sms:
        send_passport_link_to_participant(
            person, "Example workshop", "https://suaplus.example/p/token/"
        )
    email.assert_called_once()
    sms.assert_not_called()

    Consent.objects.create(
        person=person,
        type="sms",
        given_by="Consent Recipient",
        notice_version="1.0",
    )
    with patch("apps.notifications.services.send_notification"), patch(
        "apps.notifications.services.send_sms"
    ) as sms:
        send_passport_link_to_participant(
            person, "Example workshop", "https://suaplus.example/p/token/"
        )
    sms.assert_called_once()
