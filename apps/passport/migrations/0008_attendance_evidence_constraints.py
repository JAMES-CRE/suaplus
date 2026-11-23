"""Apply constraints after the data repair transaction has committed."""
from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies: ClassVar[list] = [("passport", "0007_attendance_evidence_integrity")]
    operations: ClassVar[list] = [
        migrations.AddConstraint(
            model_name="evidence",
            constraint=models.UniqueConstraint(
                fields=("source_id", "competency"),
                condition=models.Q(type="attendance", source_type="attendance", source_id__isnull=False),
                name="uniq_attendance_competency_evidence",
            ),
        ),
        migrations.AddConstraint(
            model_name="evidence",
            constraint=models.CheckConstraint(
                condition=~models.Q(type="attendance", status="valid") | models.Q(source_type="attendance", source_id__isnull=False),
                name="valid_attendance_evidence_has_source",
            ),
        ),
    ]
