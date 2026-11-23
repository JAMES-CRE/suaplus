from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.audit.models import AuditEvent
from apps.imports.services import undo_import
from apps.library.models import SurveyTemplate
from apps.passport.models import Evidence
from apps.registry.models import IdentityConflict, Person
from apps.registry.workflows import apply_import
from apps.reporting.test_calculations import attend, session
from apps.reporting.test_calculations import graph as graph_fixture

graph = graph_fixture

from apps.sessions.models import ImportBatch, SurveyResponse
from apps.sessions.services import grant_attendance_evidence


@pytest.fixture
def imported(graph):
    user = get_user_model().objects.create_user(username="undo-owner")
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=graph.programme.pk,
    )
    workshop = session(graph, "Imported")
    from apps.sessions.models import SessionCompetency

    SessionCompetency.objects.create(session=workshop, competency=graph.skill)
    batch = ImportBatch.objects.create(
        session=workshop, created_by=user, status="committed"
    )
    attendance = attend(graph.person, workshop)
    attendance.method = "import"
    attendance.import_batch = batch
    attendance.save()
    grant_attendance_evidence(attendance)
    return SimpleNamespace(**locals())


def test_undo_preserves_people_and_sources_and_is_idempotent(imported):
    g = imported
    count = Person.objects.count()
    _, changed = undo_import(g.batch.pk, g.user, reason="Wrong spreadsheet")
    assert changed
    g.attendance.refresh_from_db()
    assert g.attendance.status == "withdrawn"
    assert g.attendance.check_out_at == g.workshop.end_at
    assert Person.objects.count() == count
    assert Evidence.objects.get().status == "revoked"
    assert undo_import(g.batch.pk, g.user, reason="Replay")[1] is False
    assert AuditEvent.objects.filter(action="import_undone").count() == 1


def test_undo_blocks_later_checkout_atomically(imported):
    g = imported
    template = SurveyTemplate.objects.create(
        name="Undo exit", type="exit", definition={}
    )
    SurveyResponse.objects.create(
        attendance=g.attendance, template=template, type="exit", csat=5
    )
    with pytest.raises(ValidationError):
        undo_import(g.batch.pk, g.user, reason="Wrong batch")
    g.batch.refresh_from_db()
    g.attendance.refresh_from_db()
    assert g.batch.status == "committed"
    assert g.attendance.status == "completed"
    assert Evidence.objects.get().status == "valid"


def test_undo_rejects_pending_and_late_resolution(imported):
    g = imported
    conflict = IdentityConflict.objects.create(
        source="import",
        fingerprint="undo",
        reason="Ambiguous",
        session=g.workshop,
        payload={"identity": {}, "context": {"batch_id": g.batch.pk}},
    )
    undo_import(g.batch.pk, g.user, reason="Wrong batch")
    conflict.refresh_from_db()
    assert conflict.status == "rejected"
    with pytest.raises(ValidationError):
        apply_import(g.graph.person, g.workshop, {"batch_id": g.batch.pk})


def test_undo_get_has_no_effect_and_outsider_denied(client, imported):
    g = imported
    force_login_with_mfa(client, g.user)
    endpoint = reverse("import_undo", args=[g.batch.pk])
    assert client.get(endpoint).status_code == 405
    assert (
        client.get(reverse("import_history", args=[g.workshop.pk])).status_code == 200
    )
    outsider = get_user_model().objects.create_user(username="undo-outsider")
    with pytest.raises(PermissionDenied):
        undo_import(g.batch.pk, outsider, reason="No permission")
    assert client.post(endpoint, {"reason": ""}).status_code == 302
    g.batch.refresh_from_db()
    assert g.batch.status == "committed"
    assert client.post(endpoint, {"reason": "Wrong batch"}).status_code == 302


def test_undo_refuses_changed_source_snapshot(imported):
    g = imported
    g.batch.counts = {
        "attendance_sources": {
            str(g.attendance.pk): {
                "status": "partial",
                "check_in_at": g.attendance.check_in_at.isoformat(),
                "check_out_at": None,
            }
        }
    }
    g.batch.save()
    with pytest.raises(ValidationError):
        undo_import(g.batch.pk, g.user, reason="Later completed source")
    g.batch.refresh_from_db()
    assert g.batch.status == "committed"


def test_undo_keeps_unrelated_attendance_and_evidence(imported):
    g = imported
    other = Person.objects.create(
        full_name="Independent participant", affiliation="staff"
    )
    independent = attend(other, g.workshop)
    grant_attendance_evidence(independent)
    undo_import(g.batch.pk, g.user, reason="Wrong list")
    independent.refresh_from_db()
    assert independent.status == "completed"
    assert Evidence.objects.get(person=other).status == "valid"


@pytest.mark.django_db(transaction=True)
def test_concurrent_undo_is_one_audit_event(imported):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections, connection

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL source locks")

    def reverse_once(_index):
        close_old_connections()
        try:
            return undo_import(
                imported.batch.pk, imported.user, reason="Concurrent undo"
            )[1]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = list(pool.map(reverse_once, range(2)))
    assert sorted(changed) == [False, True]
    assert AuditEvent.objects.filter(action="import_undone").count() == 1


def test_focal_cannot_reverse_mixed_unit_batch(imported):
    from apps.registry.models import OrgUnit

    user = get_user_model().objects.create_user(username="undo-focal")
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="focal_person",
        scope_type="unit",
        scope_id=imported.graph.college.pk,
    )
    foreign_unit = OrgUnit.objects.create(
        name="Undo foreign department", type="department"
    )
    foreign = Person.objects.create(
        full_name="Foreign participant", affiliation="staff", unit=foreign_unit
    )
    row = attend(foreign, imported.workshop)
    row.method = "import"
    row.import_batch = imported.batch
    row.save()
    with pytest.raises(PermissionDenied):
        undo_import(imported.batch.pk, user, reason="Cannot touch foreign rows")
    imported.attendance.refresh_from_db()
    assert imported.attendance.status == "completed"
