"""Notification service - handles sending emails and recording them in the DB."""

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.notifications.models import Notification, NotificationTemplate


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
        notification.status = "failed"
        notification.last_error = str(e)
        notification.save(update_fields=["status", "last_error"])
        print(f"❌ Failed to send '{template_key}' to {person.email}: {e}")

    return notification


def send_sms(person, message):
    """Send an SMS if the provider is configured and the person has a phone."""
    from django.conf import settings
    import requests
    from apps.notifications.models import Notification

    if not person.phone:
        return None

    # If no provider is configured, just print to terminal (dev mode)
    if not settings.SMS_PROVIDER_URL or not settings.SMS_PROVIDER_KEY:
        print(f" [SMS to {person.phone}]: {message}")
        Notification.objects.create(
            person=person,
            channel="sms",
            template_key="sms_placeholder",
            status="sent",
            sent_at=timezone.now(),
        )
        return

    # Real provider logic (placeholder)
    try:
        response = requests.post(
            settings.SMS_PROVIDER_URL,
            headers={"Authorization": f"Bearer {settings.SMS_PROVIDER_KEY}"},
            json={"to": person.phone, "message": message},
            timeout=10,
        )
        response.raise_for_status()
        status = "sent"
    except Exception as e:
        print(f"❌ SMS failed: {e}")
        status = "failed"

    Notification.objects.create(
        person=person,
        channel="sms",
        template_key="sms_alert",
        status=status,
        sent_at=timezone.now() if status == "sent" else None,
    )