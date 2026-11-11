

from django.conf import settings
from django.db import models

from apps.core.models import AcademicPeriod
from apps.programmes.models import Programme
from apps.registry.models import OrgUnit


class Target(models.Model):
    """A KPI target value per programme / unit / category / period."""

    KPI_CHOICES = [
        ("coverage", "Coverage"),
        ("reach", "Reach"),
        ("progression", "Progression"),
        ("cascade_multiplier", "Cascade multiplier"),
        ("trainer_pipeline", "Trainer pipeline"),
        ("csat", "CSAT"),
        ("practice_adoption", "Practice adoption"),
        ("external_reach", "External reach"),
        ("target_attainment", "Target attainment"),
    ]

    kpi = models.CharField(max_length=30, choices=KPI_CHOICES)
    programme = models.ForeignKey(
        Programme,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    unit = models.ForeignKey(
        OrgUnit,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    category = models.CharField(max_length=20, blank=True, default="")
    period = models.ForeignKey(
        AcademicPeriod,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    value = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["kpi", "period"]
        indexes = [
            models.Index(fields=["kpi", "period"]),
            models.Index(fields=["programme"]),
            models.Index(fields=["unit"]),
        ]

    def __str__(self):
        return f"{self.get_kpi_display()} = {self.value}"


class Memo(models.Model):
    """An executive memo draft."""

    period_label = models.CharField(max_length=60)
    scope = models.JSONField(default=dict, blank=True)
    figures = models.JSONField()
    narrative = models.TextField(blank=True, default="")
    used_ai = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="memos_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Memo v{self.version} - {self.period_label}"


class ExportLog(models.Model):
    """A record of every export of dashboard or list data."""

    FORMAT_CHOICES = [
        ("csv", "CSV"),
        ("xlsx", "XLSX"),
        ("pdf", "PDF"),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="exports_made",
    )
    view_name = models.CharField(max_length=100)
    filters = models.JSONField(default=dict, blank=True)
    format = models.CharField(max_length=10, choices=FORMAT_CHOICES)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at"]
        indexes = [models.Index(fields=["actor", "at"])]

    def __str__(self):
        return f"{self.actor} exported {self.view_name} ({self.format})"