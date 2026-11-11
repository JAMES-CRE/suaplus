"""Library models - frameworks, domains, competencies, levels, crosswalks."""

from django.db import models


class Framework(models.Model):
    """A competency framework - KNUST's own or an external standard."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("active", "Active"),
        ("retired", "Retired"),
    ]

    code = models.CharField(
        max_length=10,
        unique=True,
        help_text="Short code, e.g. KNUST, UT, US, DC, DE",
    )
    name = models.CharField(max_length=200)
    publisher = models.CharField(max_length=200, blank=True, default="")
    version = models.CharField(max_length=30, blank=True, default="")
    source = models.CharField(
        max_length=10,
        choices=[
            ("csv", "CSV"),
            ("case_json", "IMS CASE JSON"),
            ("built_in", "Built-in"),
            ("manual", "Manual"),
        ],
        default="manual",
    )
    is_knust = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_knust", "code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Domain(models.Model):
    """A grouping inside a framework, e.g. 'AI Competency'."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("active", "Active"),
        ("retired", "Retired"),
    ]

    framework = models.ForeignKey(
        Framework, on_delete=models.PROTECT, related_name="domains"
    )
    code = models.CharField(max_length=10)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default="")
    colour = models.CharField(max_length=7, default="#002147")
    display_order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    external_alignment = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["framework", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["framework", "code"], name="uniq_domain_code"
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Competency(models.Model):
    """An individual competency, e.g. AI-01, DC-02, FT-01."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("active", "Active"),
        ("retired", "Retired"),
    ]

    framework = models.ForeignKey(
        Framework, on_delete=models.PROTECT, related_name="competencies"
    )
    domain = models.ForeignKey(
        Domain, on_delete=models.PROTECT, related_name="competencies"
    )
    code = models.CharField(max_length=20)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    proficient_descriptor = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    replaced_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaces",
        help_text="Set when this competency is retired",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["framework", "domain", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["framework", "code"], name="uniq_competency_code"
            ),
        ]

    def __str__(self):
        return f"{self.code} - {self.title}"


class Level(models.Model):
    """A proficiency level per framework, 1–5 for KNUST scale."""

    framework = models.ForeignKey(
        Framework, on_delete=models.PROTECT, related_name="levels"
    )
    ordinal = models.PositiveSmallIntegerField(
        help_text="1–5 (KNUST scale: Aware, Developing, Proficient, Advanced, Leading)"
    )
    label = models.CharField(max_length=40)
    descriptor = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["framework", "ordinal"]
        constraints = [
            models.UniqueConstraint(
                fields=["framework", "ordinal"], name="uniq_level_ordinal"
            ),
        ]

    def __str__(self):
        return f"Level {self.ordinal} - {self.label}"


class Crosswalk(models.Model):
    """Maps a KNUST competency to an external competency (with optional level)."""

    knust_competency = models.ForeignKey(
        Competency,
        on_delete=models.PROTECT,
        related_name="crosswalks_from",
    )
    external_competency = models.ForeignKey(
        Competency,
        on_delete=models.PROTECT,
        related_name="crosswalks_to",
    )
    external_level = models.ForeignKey(
        Level, null=True, blank=True, on_delete=models.SET_NULL
    )
    note = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["knust_competency", "external_competency"]
        constraints = [
            models.UniqueConstraint(
                fields=["knust_competency", "external_competency", "external_level"],
                name="uniq_crosswalk",
            ),
        ]

    def __str__(self):
        return f"{self.knust_competency.code} → {self.external_competency.code}"


class EvidenceRule(models.Model):
    """Caps the level any evidence type can support for a competency."""

    EVIDENCE_TYPE_CHOICES = [
        ("self", "Self-assessment"),
        ("task", "Workshop task/quiz"),
        ("assessor", "Facilitator/assessor"),
        ("artefact", "Reviewed artefact"),
        ("external", "External certification"),
        ("attendance", "Workshop attendance"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("active", "Active"),
        ("retired", "Retired"),
    ]

    evidence_type = models.CharField(max_length=20, choices=EVIDENCE_TYPE_CHOICES)
    description = models.CharField(max_length=300, blank=True, default="")
    max_level = models.PositiveSmallIntegerField(
        help_text="Highest level this evidence type can support (1–5)"
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    competency = models.ForeignKey(
        Competency,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="evidence_rules",
        help_text="Leave null for a rule that applies to all competencies",
    )

    class Meta:
        ordering = ["evidence_type", "competency"]

    def __str__(self):
        scope = self.competency.code if self.competency else "all competencies"
        return f"{self.get_evidence_type_display()} → max {self.max_level} ({scope})"
class SurveyTemplate(models.Model):
    """Survey question sets, editable and versioned."""

    TYPE_CHOICES = [
        ("pre", "Pre-session"),
        ("exit", "Exit"),
        ("follow_up", "Follow-up"),
    ]

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("published", "Published"),
        ("active", "Active"),
        ("retired", "Retired"),
    ]

    name = models.CharField(max_length=150)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    definition = models.JSONField(
        help_text="Survey questions as JSON"
    )
    version = models.PositiveIntegerField(default=1)
    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["type", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_type_display()})"  



class CertificationCatalogue(models.Model):
    """Sanctioned certifications that map to KNUST competencies."""

    issuer = models.CharField(max_length=150)
    title = models.CharField(max_length=200)
    verification_method = models.CharField(max_length=200, blank=True, default="")
    renewal_months = models.PositiveSmallIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    auto_approve = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["issuer", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["issuer", "title"], name="uniq_certification"
            ),
        ]

    def __str__(self):
        return f"{self.issuer} - {self.title}"


class CertificationCompetency(models.Model):
    """Which competencies a sanctioned certification proves, and at what max level."""

    certification = models.ForeignKey(
        CertificationCatalogue,
        on_delete=models.CASCADE,
        related_name="competencies",
    )
    competency = models.ForeignKey(
        Competency, on_delete=models.CASCADE, related_name="certifications"
    )
    max_level = models.PositiveSmallIntegerField(default=3)

    class Meta:
        ordering = ["certification", "competency"]
        constraints = [
            models.UniqueConstraint(
                fields=["certification", "competency"],
                name="uniq_cert_comp",
            ),
        ]

    def __str__(self):
        return f"{self.certification.title} → {self.competency.code} (L{self.max_level})"      