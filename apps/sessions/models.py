"""Sessions models  workshops, facilitation, attendance, surveys, imports."""

import secrets

from django.conf import settings
from django.db import models

from apps.library.models import Competency, SurveyTemplate
from apps.programmes.models import Partner, Programme
from apps.registry.models import OrgUnit, Person


def generate_session_code():
    """Return a 6-char code unique among active sessions."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


class Session(models.Model):
    """A single dated workshop instance."""

    MODE_CHOICES = [
        ("in_person", "In person"),
        ("hybrid", "Hybrid"),
        ("online", "Online"),
    ]

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("open", "Open"),
        ("closed", "Closed"),
        ("cancelled", "Cancelled"),
    ]

    programme = models.ForeignKey(
        Programme, on_delete=models.PROTECT, related_name="sessions"
    )
    title = models.CharField(max_length=200)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    venue = models.CharField(max_length=200, blank=True, default="")
    online_link = models.CharField(max_length=200, blank=True, default="")
    host_unit = models.ForeignKey(
        OrgUnit,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hosted_sessions",
    )
    region = models.CharField(max_length=60, blank=True, default="")
    district = models.CharField(max_length=80, blank=True, default="")
    session_code = models.CharField(
        max_length=6, unique=True, default=generate_session_code
    )
    qr_secret = models.CharField(max_length=64, default=secrets.token_hex)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="scheduled"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at"]
        indexes = [
            models.Index(fields=["programme", "start_at"]),
            models.Index(fields=["start_at"]),
            models.Index(fields=["host_unit"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.start_at:%d %b %Y})"


class SessionCompetency(models.Model):
    """Which competencies this session covers."""

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="competencies"
    )
    competency = models.ForeignKey(
        Competency, on_delete=models.PROTECT, related_name="session_coverages"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "competency"], name="uniq_session_competency"
            ),
        ]

    def __str__(self):
        return f"{self.session} - {self.competency.code}"


class SessionPartner(models.Model):
    """Which partners are tagged on this session."""

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="partners"
    )
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="sessions"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "partner"], name="uniq_session_partner"
            ),
        ]

    def __str__(self):
        return f"{self.session} + {self.partner.organisation.name}"


class Facilitation(models.Model):
    """Who facilitated a session."""

    ROLE_CHOICES = [
        ("lead", "Lead facilitator"),
        ("co_facilitator", "Co-facilitator"),
        ("associate", "Associate trainer"),
        ("external", "External facilitator"),
    ]

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="facilitators"
    )
    person = models.ForeignKey(
        Person, on_delete=models.PROTECT, related_name="facilitations"
    )
    role = models.CharField(max_length=15, choices=ROLE_CHOICES)
    external_name = models.CharField(max_length=200, blank=True, default="")
    external_phone = models.CharField(max_length=20, blank=True, default="")
    external_org = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "person"], name="uniq_facilitation"
            ),
        ]
        indexes = [models.Index(fields=["person"])]

    def __str__(self):
        return f"{self.person.full_name} - {self.get_role_display()} - {self.session}"


class ImportMappingTemplate(models.Model):
    """Saved column-mapping template for spreadsheet imports."""

    name = models.CharField(max_length=100)
    column_map = models.JSONField(
        help_text="Mapping of target field → source column name"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="import_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ImportBatch(models.Model):
    """Record of an uploaded spreadsheet."""

    STATUS_CHOICES = [
        ("preview", "Preview"),
        ("committed", "Committed"),
        ("undone", "Undone"),
    ]

    session = models.ForeignKey(
        Session,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="import_batches",
    )
    file_ref = models.CharField(max_length=300, blank=True, default="")
    mapping_template = models.ForeignKey(
        ImportMappingTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    counts = models.JSONField(
        default=dict,
        help_text="{'new': n, 'matched': n, 'conflict': n, 'error': n}",
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="preview"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="import_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["session"]),
            models.Index(fields=["created_by"]),
        ]

    def __str__(self):
        return f"Import {self.pk} - {self.get_status_display()}"


class Attendance(models.Model):
    """A person's attendance at a session."""

    METHOD_CHOICES = [
        ("qr", "QR code"),
        ("code", "Session code"),
        ("import", "Spreadsheet import"),
        ("manual", "Manual"),
    ]

    STATUS_CHOICES = [
        ("completed", "Completed"),
        ("partial", "Partial"),
        ("absent", "Absent"),
        ("withdrawn", "Withdrawn"),
    ]

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="attendances"
    )
    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="attendances"
    )
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    check_in_at = models.DateTimeField(null=True, blank=True)
    check_out_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    conflict = models.BooleanField(
        default=False,
        help_text="Set by the import wizard when a row matches multiple persons",
    )
    import_batch = models.ForeignKey(
        ImportBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendances",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "person"], name="uniq_one_checkin_per_session"
            ),
            models.CheckConstraint(
                check=(
                    models.Q(
                        status="completed",
                        check_in_at__isnull=False,
                        check_out_at__isnull=False,
                    )
                    | models.Q(
                        status="partial",
                        check_in_at__isnull=False,
                        check_out_at__isnull=True,
                    )
                    | models.Q(status__in=["absent", "withdrawn"])
                ),
                name="attendance_times_consistent",
            ),
        ]
        indexes = [
            models.Index(fields=["person", "status"]),
            models.Index(fields=["session", "status"]),
            models.Index(fields=["import_batch"]),
        ]

    def __str__(self):
        return (
            f"{self.person.full_name} @ {self.session} "
            f"({self.get_status_display()})"
        )


class SurveyResponse(models.Model):
    """Exit / pre / follow-up survey answers."""

    TYPE_CHOICES = [
        ("pre", "Pre-session"),
        ("exit", "Exit"),
        ("follow_up", "Follow-up (90 days)"),
    ]

    attendance = models.ForeignKey(
        Attendance, on_delete=models.CASCADE, related_name="survey_responses"
    )
    template = models.ForeignKey(
        SurveyTemplate, on_delete=models.PROTECT, related_name="responses"
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    csat = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="CSAT 1–5"
    )
    answers = models.JSONField(default=dict, blank=True)
    interested_in_training = models.BooleanField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["attendance", "type"], name="uniq_response_per_type"
            ),
            models.CheckConstraint(
                check=models.Q(csat__isnull=True)
                | models.Q(csat__gte=1, csat__lte=5),
                name="csat_1_to_5",
            ),
        ]

    def __str__(self):
        return f"{self.attendance.person.full_name} - {self.get_type_display()}"
    
    