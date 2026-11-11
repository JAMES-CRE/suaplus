
"""Notifications models for email/SMS templates and delivery log."""

from django.db import models

from apps.registry.models import Person


class NotificationTemplate(models.Model):
    """Editable email or SMS template."""

    CHANNEL_CHOICES = [
        ("email", "Email"),
        ("sms", "SMS"),
    ]

    key = models.CharField(
        max_length=50,
        unique=True,
        help_text="e.g. passport_link, checkin_reminder, credential_expiry",
    )
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    subject = models.CharField(max_length=200, blank=True, default="")
    body = models.TextField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} ({self.get_channel_display()})"


class Notification(models.Model):
    """A single notification delivery attempt."""

    CHANNEL_CHOICES = [
        ("email", "Email"),
        ("sms", "SMS"),
    ]
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("delivered", "Delivered"),
        ("bounced", "Bounced"),
        ("failed", "Failed"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="notifications"
    )
    template = models.ForeignKey(
        NotificationTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    template_key = models.CharField(max_length=60, blank=True, default="")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="queued"
    )
    provider_ref = models.CharField(max_length=100, blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")
    bounced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["person", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} → {self.person.full_name} ({self.get_status_display()})"