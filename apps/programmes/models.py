"""Programmes models programmes, competencies targeted, tier criteria, tiers."""

from django.conf import settings
from django.db import models

from apps.library.models import Competency
from apps.registry.models import Organisation, Person


class Programme(models.Model):
    """A training programme, e.g. 'Gemini for Education - Faculty ToT'."""

    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("active", "Active"),
        ("archived", "Archived"),
    ]

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, default="")
    audiences = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['academic_staff', 'teaching_assistants']",
    )
    delivery_modes = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['in_person', 'hybrid', 'online']",
    )
    is_template = models.BooleanField(
        default=False, help_text="If true, used as a template to clone"
    )
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="active"
    )
    cloned_from = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="clones",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ProgrammeOwner(models.Model):
    """Who owns a programme (users with manage rights)."""

    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="owners"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_programmes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["programme", "user"], name="uniq_programme_owner"
            ),
        ]

    def __str__(self):
        return f"{self.user} owns {self.programme}"


class Partner(models.Model):
    """External partner or funder for a programme (links to Organisation)."""

    ROLE_CHOICES = [
        ("funder", "Funder"),
        ("partner", "Partner"),
        ("technology_provider", "Technology provider"),
    ]

    organisation = models.ForeignKey(
        Organisation, on_delete=models.PROTECT, related_name="partnerships"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    class Meta:
        ordering = ["organisation__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "role"], name="uniq_partner_role"
            ),
        ]

    def __str__(self):
        return f"{self.organisation.name} ({self.get_role_display()})"


class ProgrammePartner(models.Model):
    """Join table: which partners are on which programme."""

    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="partners"
    )
    partner = models.ForeignKey(
        Partner, on_delete=models.CASCADE, related_name="programmes"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["programme", "partner"], name="uniq_programme_partner"
            ),
        ]

    def __str__(self):
        return f"{self.programme} + {self.partner.organisation.name}"


class ProgrammeCompetency(models.Model):
    """Which competencies a programme targets, and at what level."""

    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="competency_targets"
    )
    competency = models.ForeignKey(
        Competency, on_delete=models.PROTECT, related_name="programme_targets"
    )
    target_level = models.PositiveSmallIntegerField(
        help_text="Target level (1–5) for this competency"
    )

    class Meta:
        ordering = ["programme", "competency"]
        constraints = [
            models.UniqueConstraint(
                fields=["programme", "competency"], name="uniq_programme_competency"
            ),
        ]

    def __str__(self):
        return f"{self.programme} → {self.competency.code} (L{self.target_level})"


class TierCriterion(models.Model):
    """Rules that define a trainer tier for a programme."""

    TIER_CHOICES = [
        (1, "Explorer"),
        (2, "Practitioner"),
        (3, "Associate Trainer"),
        (4, "Certified Trainer"),
        (5, "Master Trainer"),
    ]

    RULE_TYPE_CHOICES = [
        ("min_level", "Minimum competency level"),
        ("required_credential", "Required credential"),
        ("min_sessions", "Minimum sessions led"),
        ("min_mean_csat", "Minimum mean CSAT"),
        ("recency_window", "Recency window (months)"),
    ]

    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="tier_criteria"
    )
    tier = models.PositiveSmallIntegerField(choices=TIER_CHOICES)
    rule_type = models.CharField(max_length=25, choices=RULE_TYPE_CHOICES)
    parameters = models.JSONField(
        help_text="Rule-specific params, e.g. {'competency':'AI-05','level':3}"
    )

    class Meta:
        ordering = ["programme", "tier", "rule_type"]

    def __str__(self):
        return f"{self.programme} - tier {self.tier} - {self.rule_type}"


class TierStatus(models.Model):
    """A person's tier status within a programme."""

    STATUS_CHOICES = [
        ("eligible", "Eligible"),
        ("nominated", "Nominated"),
        ("approved", "Approved"),
        ("dormant", "Dormant"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="tier_statuses"
    )
    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="tier_statuses"
    )
    tier = models.PositiveSmallIntegerField(choices=TierCriterion.TIER_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    criteria_met = models.PositiveSmallIntegerField(default=0)
    criteria_total = models.PositiveSmallIntegerField(default=0)
    progressed_criteria = models.JSONField(
        null=True, blank=True, help_text="Which criteria are met, for the passport"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_tier_statuses",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["person", "programme", "tier"], name="uniq_tier_status"
            ),
        ]
        indexes = [
            models.Index(fields=["programme", "tier", "status"]),
        ]

    def __str__(self):
        return f"{self.person.full_name} - {self.programme} - tier {self.tier}"

    # --- ADD THESE PROPERTIES INSIDE THE CLASS ---

    TIER_LABELS = ["Explorer", "Practitioner", "Associate", "Certified", "Master"]

    @property
    def steps(self):
        return [
            {
                "n": i,
                "label": label,
                "state": "done" if i < self.tier else "current" if i == self.tier else "todo"
            }
            for i, label in enumerate(self.TIER_LABELS, 1)
        ]

    @property
    def percent(self):
        if self.criteria_total > 0:
            return int((self.criteria_met / self.criteria_total) * 100)
        return 0
     

class Nomination(models.Model):
    """A nomination for a trainer tier."""

    DECISION_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    person = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="nominations"
    )
    programme = models.ForeignKey(
        Programme, on_delete=models.CASCADE, related_name="nominations"
    )
    tier = models.PositiveSmallIntegerField(choices=TierCriterion.TIER_CHOICES)
    nominated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="nominations_made",
    )
    decision = models.CharField(
        max_length=10, choices=DECISION_CHOICES, default="pending"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nominations_decided",
    )
    comments = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Nomination: {self.person.full_name} → tier {self.tier}"