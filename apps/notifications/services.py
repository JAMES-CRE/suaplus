"""Notification service - handles sending emails and recording them in the DB."""

import logging
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.utils import timezone

from apps.accounts.models import RoleAssignment
from apps.notifications.models import Notification, NotificationTemplate

logger = logging.getLogger(__name__)


def send_notification(person, template_key, context=None):
    """Send an email to a person based on a template key."""
    context = context or {}

    # 1. Fetch the template
    template = NotificationTemplate.objects.filter(
        key=template_key, channel="email", active=True
    ).first()

    if not template:
        print(f" Notification template '{template_key}' not found or inactive.")
        return None

    if not person.email:
        print(f" Cannot send notification to {person.full_name} - no email.")
        return None

    # 2. Format the subject and body
    try:
        subject = template.subject.format(**context)
        body = template.body.format(**context)
    except KeyError as e:
        print(f"⚠️ Missing context variable {e} for template '{template_key}'")
        # Fallback: strip the formatting to avoid crashing
        subject = template.subject
        body = template.body

    # 3. Create the Notification record
    notification = Notification.objects.create(
        person=person,
        template=template,
        template_key=template_key,
        channel="email",
        status="queued",
    )

    # 4. Send the email
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[person.email],
            fail_silently=False,
        )
        notification.status = "sent"
        notification.sent_at = timezone.now()
        notification.save(update_fields=["status", "sent_at"])
        print(f"✉️ Sent '{template_key}' to {person.email}")
    except Exception as e:
        logger.exception("Notification email delivery failed.")
        notification.status = "failed"
        notification.last_error = str(e)
        notification.save(update_fields=["status", "last_error"])
        print(f"❌ Failed to send '{template_key}' to {person.email}: {e}")

    return notification


def send_passport_link_to_participant(person, session_title, passport_url):
    """Deliver a passport link by email and consented SMS when available.

    Each delivery channel is recorded independently. SMS remains inactive until
    its provider credentials are configured in the deployment environment.
    """
    deliveries = []
    email_delivery = send_notification(
        person=person,
        template_key="passport_link",
        context={
            "name": person.full_name,
            "session_title": session_title,
            "passport_url": passport_url,
        },
    )
    if email_delivery:
        deliveries.append(email_delivery)

    has_sms_consent = person.consents.filter(
        type="sms", withdrawn_at__isnull=True
    ).exists()
    if has_sms_consent and person.phone:
        sms_delivery = send_sms(
            person,
            "Your Sua+ Competency Passport has been updated. Open it here: "
            f"{passport_url}",
        )
        if sms_delivery:
            deliveries.append(sms_delivery)
    return deliveries


def send_correction_request_to_facilitators(correction):
    """Notify the recorded facilitators responsible for a session correction."""
    if not correction.session_id:
        return []

    recipients = correction.session.facilitators.select_related("person").all()
    subject = "Sua+ participant correction request"
    body = (
        f"{correction.person.full_name} reported an issue with the session "
        f"{correction.session.title}.\n\n"
        f"Reported issue:\n{correction.message}\n\n"
        "Please review and correct the participant record where appropriate."
    )
    deliveries = []
    for facilitation in recipients:
        facilitator = facilitation.person
        if not facilitator.email:
            continue
        notification = Notification.objects.create(
            person=facilitator,
            template_key="passport_correction_request",
            channel="email",
            status="queued",
        )
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[facilitator.email],
                fail_silently=False,
            )
            notification.status = "sent"
            notification.sent_at = timezone.now()
            notification.save(update_fields=["status", "sent_at"])
        except Exception as exc:
            logger.exception("Notification email delivery failed.")
            notification.status = "failed"
            notification.last_error = str(exc)
            notification.save(update_fields=["status", "last_error"])
        deliveries.append(notification)
    return deliveries


def send_passport_link_to_partner_coordinators(person, session_title, passport_url):
    """Send a minor participant's passport link to coordinators for their org.

    The participant's own email address is deliberately not used here. Returns
    the number of coordinator emails successfully sent, or zero when no scoped
    coordinator with an email address is configured.
    """
    if not person.organisation_id:
        return 0

    recipient_emails = set(
        RoleAssignment.objects.filter(
            role="partner_coordinator",
            scope_type="organisation",
            scope_id=person.organisation_id,
        )
        .select_related("user_account__user")
        .values_list("user_account__user__email", flat=True)
    )
    recipient_emails = sorted(email for email in recipient_emails if email)
    if not recipient_emails:
        return 0

    sent_count = 0
    subject = "Sua+ passport link for a programme participant"
    body = (
        f"A participant associated with {person.organisation.name} has completed "
        f"{session_title}. Their secure Sua+ Competency Passport link is:\n\n"
        f"{passport_url}\n\n"
        "Please share this link with the participant in line with the consent "
        "provided by their guardian or school."
    )

    for email in recipient_emails:
        notification = Notification.objects.create(
            person=person,
            template_key="passport_link_partner_coordinator",
            channel="email",
            status="queued",
        )
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            notification.status = "sent"
            notification.sent_at = timezone.now()
            notification.save(update_fields=["status", "sent_at"])
            sent_count += 1
        except Exception as exc:
            logger.exception("Notification email delivery failed.")
            notification.status = "failed"
            notification.last_error = str(exc)
            notification.save(update_fields=["status", "last_error"])

    return sent_count


def _normalise_guardian_phone(value):
    """Accept Ghana local numbers or international E.164-style numbers."""
    value = str(value or "").strip()
    digits = re.sub(r"\D", "", value)
    if value.startswith("+") and 9 <= len(digits) <= 15:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 10:
        return f"+233{digits[1:]}"
    if len(digits) == 9:
        return f"+233{digits}"
    return ""


def send_passport_link_to_guardian(person, session_title, passport_url):
    """Send a minor's passport link to the contact recorded with guardian consent.

    Returns True only when delivery succeeds. Invalid or missing contact details
    return False so checkout can use the organisation's coordinator fallback.
    """
    consent = person.consents.filter(
        type="guardian", withdrawn_at__isnull=True
    ).exclude(contact_details="").order_by("-given_at").first()
    if not consent:
        return False

    contact = consent.contact_details.strip()
    subject = "Sua+ passport link for your child"
    body = (
        f"Hello {consent.given_by},\n\n"
        f"{person.full_name} has completed {session_title}. Their secure Sua+ "
        f"Competency Passport link is:\n\n{passport_url}\n\n"
        "Please keep this link secure.\nThe Sua+ Team"
    )

    try:
        validate_email(contact)
    except ValidationError:
        phone = _normalise_guardian_phone(contact)
        if not phone:
            return False
        notification = send_sms(person, body, recipient_phone=phone)
        return bool(notification and notification.status == "sent")

    notification = Notification.objects.create(
        person=person,
        template_key="passport_link_guardian",
        channel="email",
        status="queued",
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[contact],
            fail_silently=False,
        )
        notification.status = "sent"
        notification.sent_at = timezone.now()
        notification.save(update_fields=["status", "sent_at"])
        return True
    except Exception as exc:
        logger.exception("Guardian notification email delivery failed.")
        notification.status = "failed"
        notification.last_error = str(exc)
        notification.save(update_fields=["status", "last_error"])
        return False


def send_sms(person, message, recipient_phone=None):
    """Attempt delivery through the configured SMS provider and log the result."""
    import requests

    phone = recipient_phone or person.phone
    if not phone:
        return None

    notification = Notification.objects.create(
        person=person,
        channel="sms",
        template_key="sms_alert",
        status="queued",
    )

    if not settings.SMS_PROVIDER_URL or not settings.SMS_PROVIDER_KEY:
        notification.status = "failed"
        notification.last_error = "SMS provider is not configured."
        notification.attempts = 1
        notification.save(update_fields=["status", "last_error", "attempts"])
        return notification

    try:
        response = requests.post(
            settings.SMS_PROVIDER_URL,
            headers={"Authorization": f"Bearer {settings.SMS_PROVIDER_KEY}"},
            json={"to": phone, "message": message},
            timeout=10,
        )
        response.raise_for_status()
        notification.status = "sent"
        notification.sent_at = timezone.now()
        notification.attempts = 1
        try:
            provider_data = response.json()
        except ValueError:
            provider_data = {}
        if isinstance(provider_data, dict):
            notification.provider_ref = str(
                provider_data.get("id")
                or provider_data.get("message_id")
                or provider_data.get("reference")
                or ""
            )[:100]
        notification.save(
            update_fields=["status", "sent_at", "attempts", "provider_ref"]
        )
    except requests.RequestException as exc:
        notification.status = "failed"
        notification.last_error = str(exc)
        notification.attempts = 1
        notification.save(update_fields=["status", "last_error", "attempts"])

    return notification
