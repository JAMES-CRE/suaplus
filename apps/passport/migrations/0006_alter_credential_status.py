from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("passport", "0005_learninggoal"),
    ]

    operations = [
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
