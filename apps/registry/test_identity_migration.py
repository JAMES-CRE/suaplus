"""Identity-index backfill preserves legacy contact data and merge aliases."""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.mark.django_db(transaction=True)
def test_identity_index_migration_preserves_original_fields():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old_targets = [(app, {"registry": "0003_consent_contact_details", "passport": "0008_attendance_evidence_constraints"}.get(app, name)) for app, name in latest]
    executor.migrate(old_targets)
    try:
        old_apps = executor.loader.project_state(old_targets).apps
        Person = old_apps.get_model("registry", "Person")
        Organisation = old_apps.get_model("registry", "Organisation")
        organisation = Organisation.objects.create(name=" Example   School ", type="senior_high_school")
        raw = {"email": " Legacy@Example.ORG ", "phone": "024 123 4567", "full_name": " Legacy   Person ", "staff_or_student_no": " st f001 "}
        person = Person.objects.create(**raw, affiliation="external", organisation=organisation)
        erased = Person.objects.create(full_name="Erased", affiliation="staff", email="erased@knust.edu.gh", is_anonymised=True)
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        CurrentPerson = apps.get_model("registry", "Person")
        current = CurrentPerson.objects.get(pk=person.pk)
        for name, value in raw.items():
            assert getattr(current, name) == value
        assert current.identity_email == "legacy@example.org"
        assert current.identity_phone == "+233241234567"
        assert current.identity_name == "legacy person"
        assert current.identity_number == "STF001"
        assert CurrentPerson.objects.get(pk=erased.pk).identity_email == ""
        assert apps.get_model("registry", "Organisation").objects.get(pk=organisation.pk).name_key == "example school"
    finally:
        MigrationExecutor(connection).migrate(latest)
