"""Repair legacy check-in claims before enforcing attendance provenance."""
from typing import ClassVar

from django.db import migrations, models


def repair_attendance_evidence(apps, schema_editor):
    alias = schema_editor.connection.alias
    Evidence = apps.get_model("passport", "Evidence")
    Attendance = apps.get_model("training", "Attendance")
    Level = apps.get_model("library", "Level")
    AuditEvent = apps.get_model("audit", "AuditEvent")
    canonical = set()
    rows = Evidence.objects.using(alias).filter(type="attendance").select_related(
        "competency__framework"
    ).order_by(models.Case(models.When(status="valid", then=0), default=1), "pk")
    tracked = ("source_type", "source_id", "session_id", "level_id", "status", "assessed_at", "credential_id")
    for evidence in rows.iterator(chunk_size=1000):
        attendance = None
        if evidence.source_id is not None:
            attendance = Attendance.objects.using(alias).filter(pk=evidence.source_id, person_id=evidence.person_id).first()
            if attendance and evidence.session_id is not None and attendance.session_id != evidence.session_id:
                attendance = None
        if attendance is None and evidence.session_id is not None:
            attendance = Attendance.objects.using(alias).filter(person_id=evidence.person_id, session_id=evidence.session_id).first()
        updates = {}
        if attendance is None:
            updates = {"status": "revoked", "source_id": None}
        else:
            key = (attendance.pk, evidence.competency_id)
            if key in canonical:
                # Retain duplicate history without giving it another valid claim.
                updates = {"status": "revoked", "source_id": None}
            else:
                canonical.add(key)
                updates = {"source_type": "attendance", "source_id": attendance.pk, "session_id": attendance.session_id, "credential_id": None}
                level = Level.objects.using(alias).filter(framework_id=evidence.competency.framework_id, ordinal=1).first()
                eligible = (
                    attendance.status == "completed" and attendance.check_in_at is not None
                    and attendance.check_out_at is not None and level is not None
                    and evidence.competency.framework.is_knust
                )
                if eligible:
                    updates["level_id"] = level.pk
                    updates["assessed_at"] = attendance.check_out_at
                else:
                    updates["status"] = "revoked"
        changed = {field: value for field, value in updates.items() if getattr(evidence, field) != value}
        if not changed:
            continue
        before = {field: getattr(evidence, field) for field in tracked}
        after = {**before, **changed}
        for snapshot in (before, after):
            if snapshot["assessed_at"] is not None:
                snapshot["assessed_at"] = snapshot["assessed_at"].isoformat()
        Evidence.objects.using(alias).filter(pk=evidence.pk).update(**changed)
        AuditEvent.objects.using(alias).create(
            action="attendance_evidence_repair", entity="Evidence", entity_id=str(evidence.pk),
            before=before, after=after,
        )


class Migration(migrations.Migration):
    dependencies: ClassVar[list] = [
        ("passport", "0006_alter_credential_status"),
        ("training", "0001_initial"),
        ("audit", "0001_initial"),
    ]
    operations: ClassVar[list] = [
        migrations.RunPython(repair_attendance_evidence, migrations.RunPython.noop),
    ]
