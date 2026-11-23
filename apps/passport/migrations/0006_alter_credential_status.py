from typing import ClassVar

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies: ClassVar[list] = [
        ("passport", "0005_learninggoal"),
    ]

    operations: ClassVar[list] = [
        migrations.AlterField(
            model_name="credential",
            name="status",
            field=models.CharField(
                choices=[
                    ("valid", "Valid"),
                    ("expired", "Expired"),
                    ("revoked", "Revoked"),
                ],
                default="valid",
                max_length=8,
            ),
        ),
    ]
