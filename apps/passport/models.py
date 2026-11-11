"""Passport models - evidence, credentials, self-assessment, corrections."""

from django.conf import settings
from django.db import models

from apps.library.models import (
    Competency,
    Level,
    SurveyTemplate,
)
from apps.registry.models import Person
from apps.sessions.models import Session


class Credential(models.Model):
    """An external certification or KNUST badge held by a person."""

    KIND_CHOICES = [
        ("knust_badge", "KNUST badge"),
        ("external", "External certification"),
    ]
    SOURCE_CHOICES = [
        ("staff_recorded", "Recorded by staff"),
        ("self_submitted", "Self-submitted"),
    ]
    REVIEW_STATUS_CHOICES = [
        ("submitted", "Submitted"),
        ("in_review", "In review"),
        ("approved", "Approved"),
        ("returned", "Returned for information"),
        ("rejected", "Rejected"),
    ]
    STATUS_CHOICES = [
        ("valid", "Valid"),
        ("revoked", "Revoked"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="credentials"
    )
    catalogue = models.ForeignKey(
        "library.CertificationCatalogue",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credentials",
    )
    
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    source = models.CharField(max_length=15, choices=SOURCE_CHOICES)
    issuer = models.CharField(max_length=150, blank=True, default="")
    title = models.CharField(max_length=200)
    credential_id = models.CharField(max_length=100, blank=True, default="")
    issued_at = models.DateField()
    expires_at = models.DateField(null=True, blank=True)
    verification_url = models.CharField(max_length=300, blank=True, default="")
    file = models.FileField(upload_to="credentials/", blank=True, default="")
    other_description = models.TextField(blank=True, default="")
    review_status = models.CharField(
        max_length=10, choices=REVIEW_STATUS_CHOICES, default="submitted"
    )
    review_note = models.TextField(blank=True, default="")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_credentials",
    )
    status = models.CharField(
        max_length=8, choices=STATUS_CHOICES, default="valid"
    )
    badge_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["person", "status"]),
            models.Index(fields=["review_status", "created_at"]),
            models.Index(fields=["issuer", "credential_id"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "credential_id"],
                condition=~models.Q(credential_id=""),
                name="uniq_credential_id_when_present",
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.person.full_name}"


class CredentialReview(models.Model):
    """History of every status change on a credential."""

    credential = models.ForeignKey(
        Credential, on_delete=models.CASCADE, related_name="reviews"
    )
    from_status = models.CharField(max_length=10, blank=True, default="")
    to_status = models.CharField(max_length=10)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credential_reviews_made",
    )
    comment = models.TextField(blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["credential", "at"])]

    def __str__(self):
        return f"{self.credential} - {self.from_status} → {self.to_status}"


class Evidence(models.Model):
    """The core record: one row per person × competency × level × evidence type."""

    TYPE_CHOICES = [
        ("self", "Self-assessment"),
        ("task", "Workshop task/quiz"),
        ("assessor", "Facilitator/assessor"),
        ("artefact", "Reviewed artefact"),
        ("external", "External certification"),
        ("attendance", "Workshop attendance"),
    ]
    SOURCE_TYPE_CHOICES = [
        ("", "None"),
        ("attendance", "Attendance"),
        ("credential", "Credential"),
        ("self_assessment", "Self-assessment"),
    ]
    STATUS_CHOICES = [
        ("valid", "Valid"),
        ("expired", "Expired"),
        ("revoked", "Revoked"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="evidence"
    )
    competency = models.ForeignKey(
        Competency, on_delete=models.PROTECT, related_name="evidence"
    )
    level = models.ForeignKey(
        Level, on_delete=models.PROTECT, related_name="evidence"
    )
    type = models.CharField(max_length=12, choices=TYPE_CHOICES)
    source_type = models.CharField(
        max_length=20, choices=SOURCE_TYPE_CHOICES, blank=True, default=""
    )
    source_id = models.BigIntegerField(null=True, blank=True)
    credential = models.ForeignKey(
        Credential,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="evidence",
    )
    session = models.ForeignKey(
        Session,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="evidence",
    )
    assessed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assessments_made",
    )
    assessed_at = models.DateTimeField(auto_now_add=True)
    framework_version = models.CharField(max_length=30, blank=True, default="")
    expires_at = models.DateField(null=True, blank=True)
    is_expired = models.BooleanField(default=False)
    status = models.CharField(
        max_length=8, choices=STATUS_CHOICES, default="valid"
    )

    class Meta:
        ordering = ["-assessed_at"]
        indexes = [
            models.Index(fields=["person", "competency", "status"]),
            models.Index(fields=["competency", "type"]),
            models.Index(fields=["credential"]),
            models.Index(fields=["session"]),
            models.Index(fields=["source_type", "source_id"]),
            models.Index(
                fields=["expires_at"],
                name="evidence_pending_expiry",
                condition=models.Q(is_expired=False),
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(type="self", source_type="self_assessment")
                    | models.Q(type="external", source_type="credential")
                    | models.Q(type="artefact", source_type="credential")
                    | models.Q(type="task", source_type="attendance")
                    | models.Q(type="assessor", source_type="attendance")
                    | models.Q(type="attendance", source_type="attendance")
                    | models.Q(source_type="")
                ),
                name="evidence_source_consistent",
            ),
        ]

    def __str__(self):
        return (
            f"{self.person.full_name} - {self.competency.code} - "
            f"L{self.level.ordinal} ({self.get_type_display()})"
        )


class SelfAssessment(models.Model):
    """A baseline self-assessment submission."""

    STATUS_CHOICES = [
        ("started", "Started"),
        ("submitted", "Submitted"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="self_assessments"
    )
    template = models.ForeignKey(
        SurveyTemplate, on_delete=models.PROTECT, related_name="self_assessments"
    )
    cycle_id = models.BigIntegerField(
        null=True, blank=True, help_text="Academic period id, if set"
    )
    answers = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="started"
    )
    needs_focal_check = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["person", "status"]),
            models.Index(fields=["template"]),
        ]

    def __str__(self):
        return f"Self-assessment - {self.person.full_name} ({self.get_status_display()})"


class CorrectionRequest(models.Model):
    """A participant's report of an error on their passport."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="correction_requests"
    )
    session = models.ForeignKey(
        Session,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="correction_requests",
    )
    message = models.TextField()
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="corrections_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["person"]),
            models.Index(fields=["session"]),
        ]

    def __str__(self):
        return f"Correction - {self.person.full_name}"