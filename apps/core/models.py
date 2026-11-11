

from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base: adds created_at and updated_at to any model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AcademicPeriod(TimeStampedModel):
    """Academic year and semester, e.g. 2025/2026 semester 1."""

    SEMESTER_CHOICES = [
        (0, "Full year"),
        (1, "First semester"),
        (2, "Second semester"),
        (3, "Summer"),
    ]

    year_start = models.PositiveSmallIntegerField(help_text="e.g. 2025")
    year_end = models.PositiveSmallIntegerField(help_text="e.g. 2026")
    semester = models.PositiveSmallIntegerField(choices=SEMESTER_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["-year_start", "semester"]
        constraints = [
            models.UniqueConstraint(
                fields=["year_start", "year_end", "semester"],
                name="uniq_academic_period",
            ),
        ]

    def __str__(self):
        return f"{self.year_start}/{self.year_end} · sem {self.semester}"