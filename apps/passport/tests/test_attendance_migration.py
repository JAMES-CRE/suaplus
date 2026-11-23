"""Exercise provenance repair on the disposable test database only."""
from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone


@pytest.mark.django_db(transaction=True)
def test_legacy_attendance_claims_are_repaired_without_deleting_history():
    library_target = ("library", "0012_alter_libraryversion_item_type_leveldraft")
    registry_target = ("registry", "0004_canonical_identity_review")
    old_target = [("passport", "0006_alter_credential_status"), ("audit", "0001_initial"), library_target, registry_target]
    new_target = [("passport", "0008_attendance_evidence_constraints"), library_target, registry_target]
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate(old_target)
    try:
        old_apps = executor.loader.project_state(old_target).apps
        Framework = old_apps.get_model("library", "Framework")
        Domain = old_apps.get_model("library", "Domain")
        Competency = old_apps.get_model("library", "Competency")
        Level = old_apps.get_model("library", "Level")
        Programme = old_apps.get_model("programmes", "Programme")
        Session = old_apps.get_model("training", "Session")
        Attendance = old_apps.get_model("training", "Attendance")
        Person = old_apps.get_model("registry", "Person")
        Evidence = old_apps.get_model("passport", "Evidence")
        framework = Framework.objects.create(code="MIGRATE", name="Migration framework", is_knust=True)
        domain = Domain.objects.create(framework=framework, code="AI", name="AI")
        skill = Competency.objects.create(framework=framework, domain=domain, code="AI-01", title="Migration skill")
        aware = Level.objects.create(framework=framework, ordinal=1, label="Aware")
        high = Level.objects.create(framework=framework, ordinal=3, label="Proficient")
        programme = Programme.objects.create(name="Migration programme")
        now = timezone.now()
        session = Session.objects.create(programme=programme, title="Migration workshop", start_at=now - timedelta(hours=2), end_at=now, mode="in_person")
        completed_person = Person.objects.create(full_name="Completed migration participant", affiliation="staff")
        partial_person = Person.objects.create(full_name="Partial migration participant", affiliation="staff")
        completed = Attendance.objects.create(person=completed_person, session=session, method="manual", status="completed", check_in_at=session.start_at, check_out_at=session.end_at)
        partial = Attendance.objects.create(person=partial_person, session=session, method="manual", status="partial", check_in_at=session.start_at)
        original = Evidence.objects.create(person=completed_person, session=session, competency=skill, level=high, type="attendance", source_type="attendance", framework_version="MIGRATE:1")
        duplicate = Evidence.objects.create(person=completed_person, session=session, competency=skill, level=high, type="attendance", source_type="attendance")
        premature = Evidence.objects.create(person=partial_person, session=session, competency=skill, level=aware, type="attendance", source_type="attendance")
        orphan = Evidence.objects.create(person=partial_person, competency=skill, level=aware, type="attendance", source_type="")
        ids = [row.pk for row in (original, duplicate, premature, orphan)]
        executor = MigrationExecutor(connection)
        executor.migrate(new_target)
        new_apps = executor.loader.project_state(new_target).apps
        CurrentEvidence = new_apps.get_model("passport", "Evidence")
        assert CurrentEvidence.objects.filter(pk__in=ids).count() == 4
        repaired = CurrentEvidence.objects.get(pk=original.pk)
        assert repaired.source_id == completed.pk
        assert repaired.level_id == aware.pk
        assert repaired.status == "valid"
        assert repaired.framework_version == "MIGRATE:1"
        assert repaired.assessed_at == completed.check_out_at
        assert CurrentEvidence.objects.get(pk=duplicate.pk).status == "revoked"
        assert CurrentEvidence.objects.get(pk=duplicate.pk).source_id is None
        premature = CurrentEvidence.objects.get(pk=premature.pk)
        assert premature.status == "revoked"
        assert premature.source_id == partial.pk
        assert CurrentEvidence.objects.get(pk=orphan.pk).status == "revoked"
        AuditEvent = new_apps.get_model("audit", "AuditEvent")
        assert AuditEvent.objects.filter(action="attendance_evidence_repair", entity_id=str(original.pk)).exists()
    finally:
        MigrationExecutor(connection).migrate(latest)
