from typing import ClassVar

from django.conf import settings
from django.db import models


class OrgUnit(models.Model):
    """Campus → college → faculty → department hierarchy."""

    TYPE_CHOICES: ClassVar[tuple[tuple[str, str], ...]] = (
        ("campus", "Campus"),
        ("college", "College"),
        ("directorate", "Directorate"),
        ("faculty", "Faculty"),
        ("school", "School"),
        ("department", "Department"),
    )

    name = models.CharField(max_length=200)
    short_name = models.CharField(max_length=30, blank=True, default="")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list] = ["type", "name"]

    def __str__(self):
        return self.name


class Organisation(models.Model):
    """External bodies - schools, universities, government, NGOs, companies."""

    TYPE_CHOICES = (
        ("senior_high_school", "Senior High School"),
        ("basic_school", "Basic School"),
        ("university", "University"),
        ("government", "Government"),
        ("ngo", "NGO"),
        ("company", "Company"),
        ("community", "Community"),
        ("other", "Other"),
    )

    name = models.CharField(max_length=250)
    name_key = models.CharField(max_length=250, blank=True, default="", db_index=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    region = models.CharField(max_length=60, blank=True, default="")
    district = models.CharField(max_length=80, blank=True, default="")
    country = models.CharField(max_length=60, default="Ghana")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from apps.registry.identifiers import normalize_name
        self.name_key = normalize_name(self.name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"name_key"}
        return super().save(*args, **kwargs)

    class Meta:
        ordering: ClassVar[list] = ["name"]

    def __str__(self):
        return self.name


class Person(models.Model):
    """One row per human - staff, student or external."""

    AFFILIATION_CHOICES = (
        ("staff", "Staff"),
        ("student", "Student"),
        ("external", "External"),
    )

    AGE_BAND_CHOICES = (
        ("under_18", "Under 18"),
        ("18_plus", "18 or over"),
        ("unknown", "Unknown"),
    )

    affiliation = models.CharField(max_length=10, choices=AFFILIATION_CHOICES)
    staff_or_student_no = models.CharField(max_length=30, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    identity_email = models.CharField(max_length=254, blank=True, default="", db_index=True)
    identity_phone = models.CharField(max_length=20, blank=True, default="", db_index=True)
    identity_number = models.CharField(max_length=30, blank=True, default="", db_index=True)
    identity_name = models.CharField(max_length=200, blank=True, default="", db_index=True)
    title = models.CharField(max_length=10, blank=True, default="")
    full_name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, blank=True, default="")
    rank_or_level = models.CharField(max_length=40, blank=True, default="")
    unit_other = models.CharField(max_length=200, blank=True, default="")
    gender = models.CharField(max_length=10, blank=True, default="")
    age_band = models.CharField(
        max_length=10, choices=AGE_BAND_CHOICES, default="unknown"
    )
    is_anonymised = models.BooleanField(default=False)
    anonymised_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_from",
    )
    organisation = models.ForeignKey(
        Organisation, null=True, blank=True, on_delete=models.SET_NULL
    )
    unit = models.ForeignKey(
        OrgUnit, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        from apps.registry.identifiers import identity_keys

        changed = set(kwargs["update_fields"]) if kwargs.get("update_fields") is not None else None
        if self.staff_or_student_no == "":
            self.staff_or_student_no = None
            if changed is not None:
                changed.add("staff_or_student_no")
        if self.phone == "" or self.age_band == "under_18":
            self.phone = None
            if changed is not None:
                changed.add("phone")
        keys = identity_keys(self)
        for field, value in keys.items():
            setattr(self, field, value)
        if changed is not None:
            sources = {"email": "identity_email", "phone": "identity_phone", "staff_or_student_no": "identity_number", "full_name": "identity_name"}
            changed |= {target for source, target in sources.items() if source in changed}
            if "is_anonymised" in changed:
                changed |= set(keys)
            if "age_band" in changed:
                changed.add("identity_phone")
            kwargs["update_fields"] = changed
        return super().save(*args, **kwargs)

    class Meta:
        ordering: ClassVar[list] = ["full_name"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["staff_or_student_no"],
                condition=models.Q(staff_or_student_no__isnull=False),
                name="uniq_staff_or_student_no",
            ),
            models.CheckConstraint(
                condition=~models.Q(age_band="under_18")
                | models.Q(phone__isnull=True),
                name="minor_no_phone",
            ),
        ]
        
        
    @property
    def initials(self):
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][0].upper()
        return (parts[0][0] + parts[-1][0]).upper()    

    def __str__(self):
                return f"{self.full_name} ({self.get_affiliation_display()})"  # type: ignore


class Consent(models.Model):
    """Ghana DPA Act 843 - evidence of consent."""

    TYPE_CHOICES = (
        ("data_processing", "Data processing"),
        ("sms", "SMS"),
        ("guardian", "Guardian consent"),
        ("school", "School consent"),
    )

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="consents"
    )
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    given_by = models.CharField(max_length=200)
    contact_details = models.CharField(max_length=100, blank=True, default="")
    given_at = models.DateTimeField(auto_now_add=True)
    notice_version = models.CharField(max_length=20)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list] = ["-given_at"]

    def __str__(self):
                return f"{self.get_type_display()} - {self.person.full_name}"  # type: ignore


class PassportToken(models.Model):
    """Personal passport link token (stored as hash)."""

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="passport_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list] = ["-created_at"]

    def __str__(self):
        return f"Token for {self.person.full_name}"


class UnitHeadcount(models.Model):
    """Semester headcount per unit and category - denominator for coverage KPI."""

    unit = models.ForeignKey(
        OrgUnit, on_delete=models.CASCADE, related_name="headcounts"
    )
    category = models.CharField(max_length=20)
    period = models.ForeignKey(
        "core.AcademicPeriod",
        on_delete=models.CASCADE,
        related_name="headcounts",
    )
    headcount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list] = ["-period__year_start", "unit", "category"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=["unit", "category", "period"],
                name="uniq_headcount",
            )
        ]

    def __str__(self):
        return f"{self.unit.name} · {self.category} · {self.period} · {self.headcount}"


class IdentityMutex(models.Model):
    """Persistent hashed lock keys; no identifier values are stored here."""

    key_hash = models.CharField(max_length=64, primary_key=True)


class IdentityConflict(models.Model):
    """An unresolved submission, not a new Person or fabricated attendance."""

    STATUS_CHOICES: ClassVar[list] = [
        ("pending", "Pending review"), ("matched", "Matched to existing person"),
        ("created", "Confirmed as a new person"), ("rejected", "Rejected"),
    ]
    source = models.CharField(max_length=20)
    fingerprint = models.CharField(max_length=64)
    reason = models.CharField(max_length=200)
    payload = models.JSONField()
    session = models.ForeignKey("training.Session", null=True, blank=True, on_delete=models.PROTECT, related_name="identity_conflicts")
    candidates = models.ManyToManyField(Person, blank=True, related_name="identity_conflicts")
    organisation_candidates = models.ManyToManyField(Organisation, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    selected_person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.PROTECT, related_name="resolved_identity_conflicts")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="identity_submissions")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="identity_resolutions")
    resolution_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list] = ["created_at", "pk"]
        constraints: ClassVar[list] = [
            models.UniqueConstraint(fields=["fingerprint"], condition=models.Q(status="pending"), name="uniq_pending_identity_submission"),
        ]
        indexes: ClassVar[list] = [models.Index(fields=["session", "status"])]

    def __str__(self):
        return f"Identity review #{self.pk} ({self.get_status_display()})"
