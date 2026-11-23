import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.programmes.models import Programme
from apps.registry.models import Person
from apps.sessions.models import Attendance, ImportMappingTemplate, Session


@pytest.mark.django_db
def test_import_preview_validates_email_name_and_conflicts(client):
    user = get_user_model().objects.create_user(
        username="admin@knust.edu.gh",
        email="admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    programme = Programme.objects.create(name="Import Test Programme")
    session = Session.objects.create(
        programme=programme,
        title="Import Test Session",
        start_at="2026-11-01T09:00:00Z",
        end_at="2026-11-01T17:00:00Z",
        mode="in_person",
        status="open",
    )
    Person.objects.create(
        affiliation="staff",
        full_name="First Match",
        email="ambiguous@example.org",
    )
    Person.objects.create(
        affiliation="staff",
        full_name="Second Match",
        email="ambiguous@example.org",
    )
    force_login_with_mfa(client, user)

    import_state = client.session
    import_state["import_session_id"] = session.id
    import_state["import_columns"] = ["Email", "Name"]
    import_state["import_data"] = [
        {"Email": "new@example.org", "Name": "New Person"},
        {"Email": "not-an-email", "Name": "Invalid Email"},
        {"Email": "missing-name@example.org", "Name": ""},
        {"Email": "new@example.org", "Name": "Duplicate Row"},
        {"Email": "ambiguous@example.org", "Name": "Ambiguous Match"},
    ]
    import_state.save()

    response = client.post(
        reverse("import_preview"),
        {"email_col": "Email", "name_col": "Name"},
    )

    assert response.status_code == 200
    assert [row["status"] for row in response.context["preview_rows"]] == [
        "new",
        "error",
        "error",
        "conflict",
        "conflict",
    ]
    assert response.context["new_count"] == 1
    assert response.context["error_count"] == 2
    assert response.context["conflict_count"] == 2
    assert "Email is duplicated in this file" in response.content.decode()
    assert "Email matches multiple people in the registry" in response.content.decode()

    commit_response = client.post(reverse("import_commit"))

    assert commit_response.status_code == 302
    assert Person.objects.filter(email="new@example.org").count() == 1
    assert Attendance.objects.filter(session=session).count() == 1
    active_attendance = Attendance.objects.get(
        session=session, person__email="new@example.org"
    )
    assert active_attendance.status == "partial"
    assert active_attendance.check_out_at is None

    session.status = "closed"
    session.save(update_fields=["status"])
    closed_import_state = client.session
    closed_import_state["import_session_id"] = session.id
    closed_import_state["import_columns"] = ["Email", "Name"]
    closed_import_state["import_data"] = [
        {"Email": "closed@example.org", "Name": "Closed Session Person"},
    ]
    closed_import_state.save()
    client.post(reverse("import_preview"), {"email_col": "Email", "name_col": "Name"})
    client.post(reverse("import_commit"))

    closed_attendance = Attendance.objects.get(
        session=session, person__email="closed@example.org"
    )
    session.refresh_from_db()
    assert closed_attendance.status == "completed"
    assert closed_attendance.check_out_at == session.end_at


@pytest.mark.django_db
def test_import_commit_issues_passport_link_for_each_new_attendance(client, monkeypatch):
    user = get_user_model().objects.create_user(
        username="passport-import-admin@knust.edu.gh",
        email="passport-import-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    programme = Programme.objects.create(name="Passport Import Programme")
    session = Session.objects.create(
        programme=programme,
        title="Passport Import Session",
        start_at="2026-11-01T09:00:00Z",
        end_at="2026-11-01T17:00:00Z",
        mode="in_person",
        status="open",
    )
    force_login_with_mfa(client, user)
    state = client.session
    state["import_session_id"] = session.id
    state["import_preview"] = [
        {"email": "passport-import@example.org", "name": "Passport Import", "status": "new", "note": ""},
    ]
    state.save()
    issued = []
    monkeypatch.setattr(
        "apps.passport.linking.issue_passport_link",
        lambda person: issued.append(person) or "https://suaplus.example/p/new/",
    )
    monkeypatch.setattr("apps.notifications.services.send_passport_link_to_participant", lambda **kwargs: [])

    response = client.post(reverse("import_commit"))

    assert response.status_code == 302
    assert [person.email for person in issued] == ["passport-import@example.org"]


@pytest.mark.django_db
def test_import_mapping_can_be_saved_and_reused(client):
    user = get_user_model().objects.create_user(
        username="mapping-admin@knust.edu.gh",
        email="mapping-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    programme = Programme.objects.create(name="Mapping Test Programme")
    session = Session.objects.create(
        programme=programme,
        title="Mapping Test Session",
        start_at="2026-11-01T09:00:00Z",
        end_at="2026-11-01T17:00:00Z",
        mode="in_person",
        status="open",
    )
    force_login_with_mfa(client, user)
    mapping = ImportMappingTemplate.objects.create(
        name="Saved list",
        column_map={"email": "Work Email", "name": "Participant"},
        created_by=user,
    )

    import_state = client.session
    import_state["import_session_id"] = session.id
    import_state["import_columns"] = ["Work Email", "Participant"]
    import_state["import_data"] = [
        {"Work Email": "reused@example.org", "Participant": "Reused Person"},
    ]
    import_state.save()

    response = client.post(
        reverse("import_preview"),
        {"mapping_template": str(mapping.id)},
    )

    assert response.status_code == 200
    assert response.context["email_col"] == "Work Email"
    assert response.context["name_col"] == "Participant"
    assert response.context["preview_rows"][0]["status"] == "new"

    import_state = client.session
    import_state["import_data"] = [
        {"Work Email": "saved@example.org", "Participant": "Saved Person"},
    ]
    import_state.save()
    client.post(
        reverse("import_preview"),
        {
            "email_col": "Work Email",
            "name_col": "Participant",
            "save_mapping": "on",
            "mapping_name": "Another saved list",
        },
    )

    saved_mapping = ImportMappingTemplate.objects.get(name="Another saved list")
    assert saved_mapping.created_by == user
    assert saved_mapping.column_map == {
        "email": "Work Email",
        "name": "Participant",
    }
