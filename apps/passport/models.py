"""Passport models - evidence, credentials, self-assessment, corrections."""
from typing import ClassVar

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

    KIND_CHOICES: ClassVar[list] = [
        ("knust_badge", "KNUST badge"),
        ("external", "External certification"),
    ]
    SOURCE_CHOICES: ClassVar[list] = [
        ("staff_recorded", "Recorded by staff"),
        ("self_submitted", "Self-submitted"),
    ]
    REVIEW_STATUS_CHOICES: ClassVar[list] = [
        ("submitted", "Submitted"),
        ("in_review", "In review"),
        ("approved", "Approved"),
        ("returned", "Returned for information"),
        ("rejected", "Rejected"),
    ]
    STATUS_CHOICES: ClassVar[list] = [
        ("valid", "Valid"),
        ("expired", "Expired"),
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
        ordering: ClassVar[list] = ["-created_at"]
        indexes: ClassVar[list] = [
            models.Index(fields=["person", "status"]),
            models.Index(fields=["review_status", "created_at"]),
            models.Index(fields=["issuer", "credential_id"]),
        ]
        constraints: ClassVar[list] = [
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
        ordering: ClassVar[list] = ["-at"]
        indexes: ClassVar[list] = [models.Index(fields=["credential", "at"])]

    def __str__(self):
        return f"{self.credential} - {self.from_status} → {self.to_status}"


class Evidence(models.Model):
    """The core record: one row per person × competency × level × evidence type."""

    TYPE_CHOICES: ClassVar[list] = [
        ("self", "Self-assessment"),
        ("task", "Workshop task/quiz"),
        ("assessor", "Facilitator/assessor"),
        ("artefact", "Reviewed artefact"),
        ("external", "External certification"),
        ("attendance", "Workshop attendance"),
    ]
    SOURCE_TYPE_CHOICES: ClassVar[list] = [
        ("", "None"),
        ("attendance", "Attendance"),
        ("credential", "Credential"),
        ("self_assessment", "Self-assessment"),
    ]
    STATUS_CHOICES: ClassVar[list] = [
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
        ordering: ClassVar[list] = ["-assessed_at"]
        indexes: ClassVar[list] = [
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
        constraints: ClassVar[list] = [
            models.CheckConstraint(
                condition=(
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
            models.UniqueConstraint(
                fields=["source_id", "competency"],
                condition=models.Q(type="attendance", source_type="attendance", source_id__isnull=False),
                name="uniq_attendance_competency_evidence",
            ),
            models.CheckConstraint(
                condition=~models.Q(type="attendance", status="valid")
                | models.Q(source_type="attendance", source_id__isnull=False),
                name="valid_attendance_evidence_has_source",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        from apps.sessions.models import Attendance

        super().clean()
        if self.type != "attendance" or self.status != "valid":
            return
        attendance = Attendance.objects.filter(pk=self.source_id).first() if self.source_id is not None else None
        if self.source_type != "attendance" or attendance is None or attendance.status != "completed":
            raise ValidationError("Attendance evidence requires a completed source attendance record.")
        if self.person_id != attendance.person_id or self.session_id != attendance.session_id:
            raise ValidationError("Evidence person and session must match its source attendance.")
        if self.level_id and self.competency_id and (
            self.level.ordinal != 1 or self.level.framework_id != self.competency.framework_id
            or not self.competency.framework.is_knust
        ):
            raise ValidationError({"level": "Workshop attendance supports KNUST level 1 only."})
        if self.credential_id is not None:
            raise ValidationError({"credential": "Attendance evidence must not use a credential as its source."})

    def __str__(self):
        return (
            f"{self.person.full_name} - {self.competency.code} - "
            f"L{self.level.ordinal} ({self.get_type_display()})"
        )


class SelfAssessment(models.Model):
    """A baseline self-assessment submission."""

    STATUS_CHOICES: ClassVar[list] = [
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
        ordering: ClassVar[list] = ["-started_at"]
        indexes: ClassVar[list] = [
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
        ordering: ClassVar[list] = ["-created_at"]
        indexes: ClassVar[list] = [
            models.Index(fields=["person"]),
            models.Index(fields=["session"]),
        ]

    def __str__(self):
        return f"Correction - {self.person.full_name}"


class PassportStampView(models.Model):
    """Records the first time a participant sees an attendance stamp."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="seen_passport_stamps"
    )
    attendance = models.ForeignKey(
        "training.Attendance", on_delete=models.CASCADE, related_name="passport_views"
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["person", "attendance"], name="uniq_passport_stamp_view"
            )
        ]


class LearningGoal(models.Model):
    """A personal development goal set by a passport holder."""

    STATUS_CHOICES: ClassVar[list] = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("archived", "Archived"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="learning_goals"
    )
    title = models.CharField(max_length=240)
    target_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="active")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ["status", "target_date", "-created_at"]

    def __str__(self):
        return f"{self.person.full_name}: {self.title}"


class AssessmentLink(models.Model):
    """Short-lived single-purpose SAS token, never a passport bearer token."""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='assessment_links')
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    profile_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class InternalAssessment(models.Model):
    """Immutable assessor provenance for an internal workshop award."""
    attendance = models.ForeignKey('training.Attendance', on_delete=models.PROTECT, related_name='internal_assessments')
    competency = models.ForeignKey(Competency, on_delete=models.PROTECT)
    level = models.ForeignKey(Level, on_delete=models.PROTECT)
    evidence_type = models.CharField(max_length=12, choices=[('task', 'Workshop task'), ('assessor', 'Assessor')])
    assessor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    note = models.TextField()
    evidence = models.OneToOneField(Evidence, on_delete=models.PROTECT)
    credential = models.OneToOneField(Credential, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['attendance', 'competency', 'evidence_type'], name='uniq_internal_source_assessment')]
