
# Create your models here.
"""Audit models append-only log of writes and bulk views."""

from django.db import models
from django.conf import settings
from django.db import models


class AuditEvent(models.Model):
    """One row per write or bulk view of personal data. Append-only."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
        help_text="Null for system-generated events",
    )
    action = models.CharField(max_length=40)
    entity = models.CharField(max_length=60)
    entity_id = models.CharField(max_length=40)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [
            models.Index(fields=["entity", "entity_id"]),
            models.Index(fields=["actor", "at"]),
        ]
        verbose_name = "Audit event"
        verbose_name_plural = "Audit events"

    def __str__(self):
        return f"{self.action} {self.entity}#{self.entity_id}"