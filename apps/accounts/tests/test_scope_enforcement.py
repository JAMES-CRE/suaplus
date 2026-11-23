"""SRS role matrix and cross-scope denial tests, with no external services."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.linking import resolve_person_for_user
from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.permissions import (
    CAPABILITIES,
    can,
    can_view_person,
    scope_records,
    session_queryset,
    unit_descendants,
)
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.library.models import Competency, Domain, Framework, Level
from apps.passport.linking import issue_passport_link
from apps.passport.models import Credential, Evidence, LearningGoal
from apps.programmes.models import (
    Nomination,
    Organisation,
    Programme,
    ProgrammeCompetency,
    TierStatus,
)
from apps.registry.models import OrgUnit, Person
from apps.reporting.access import reporting_scope
from apps.reporting.models import ExportLog, Memo
from apps.reporting.services.kpis import kpi_reach
from apps.reporting.services.memo import generate_memo
from apps.sessions.models import Attendance, Facilitation, Session


@pytest.fixture
def graph(db):
    campus = OrgUnit.objects.create(name="Campus", type="campus")
    college_a = OrgUnit.objects.create(name="College A", type="college", parent=campus)
    college_b = OrgUnit.objects.create(name="College B", type="college", parent=campus)
    faculty = OrgUnit.objects.create(name="Faculty A", type="faculty", parent=college_a)
    unit_a = OrgUnit.objects.create(name="Department A", type="department", parent=faculty)
    unit_b = OrgUnit.objects.create(name="Department B", type="department", parent=college_b)
    org_a = Organisation.objects.create(name="Partner A", type="ngo")
    org_b = Organisation.objects.create(name="Partner B", type="ngo")
    programme_a = Programme.objects.create(name="Programme A")
    programme_b = Programme.objects.create(name="Programme B")
    framework = Framework.objects.create(code="SCOPE", name="Scope framework", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    competency = Competency.objects.create(framework=framework, domain=domain, code="AI-01", title="Shared competency")
    level = Level.objects.create(framework=framework, ordinal=3, label="Proficient")
    for programme in (programme_a, programme_b):
        ProgrammeCompetency.objects.create(programme=programme, competency=competency, target_level=3)
    start = timezone.now() - timedelta(days=2)
    session_a = Session.objects.create(programme=programme_a, title="Session A", start_at=start, end_at=start + timedelta(hours=2), mode="in_person", host_unit=unit_a, status="closed")
    other_a = Session.objects.create(programme=programme_a, title="Another facilitator's session", start_at=start, end_at=start + timedelta(hours=2), mode="in_person", host_unit=unit_a, status="closed")
    session_b = Session.objects.create(programme=programme_b, title="Session B", start_at=start, end_at=start + timedelta(hours=2), mode="in_person", host_unit=unit_b, status="closed")
    people = {}
    for key, unit, org in [("a", unit_a, None), ("b", unit_b, None), ("bridge", unit_b, None), ("external_a", None, org_a), ("external_b", None, org_b)]:
        people[key] = Person.objects.create(full_name=f"PRIVATE PERSON {key}", email=f"{key}@example.org", phone="0241234567", affiliation="external" if org else "staff", unit=unit, organisation=org, title="Dr.", category="academic", age_band="18_plus")
    for key, session in [("a", session_a), ("bridge", session_a), ("external_a", session_a), ("b", session_b), ("external_b", session_b)]:
        Attendance.objects.create(person=people[key], session=session, method="manual", status="completed", check_in_at=start, check_out_at=start + timedelta(hours=2))
        Evidence.objects.create(person=people[key], competency=competency, level=level, type="external", source_type="credential", session=session)
    return SimpleNamespace(**locals())


def actor(graph, role=None, is_staff=False):
    number = get_user_model().objects.count()
    user = get_user_model().objects.create_user(username=f"actor-{number}", email=f"actor-{number}@knust.edu.gh", is_staff=is_staff)
    person = Person.objects.create(full_name=f"Actor {number}", email=user.email, affiliation="staff", title="Dr.", category="academic", unit=graph.unit_a, age_band="18_plus")
    account = UserAccount.objects.create(user=user, person=person)
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    targets = {
        "focal_person": ("unit", graph.college_a.pk), "programme_owner": ("programme", graph.programme_a.pk),
        "facilitator": ("programme", graph.programme_a.pk), "associate_trainer": ("session", graph.session_a.pk),
        "partner_coordinator": ("organisation", graph.org_a.pk), "participant": ("own", None),
        "super_admin": ("all", None), "executive_viewer": ("all", None), "library_curator": ("all", None),
    }
    if role:
        scope_type, scope_id = targets[role]
        RoleAssignment.objects.create(user_account=account, role=role, scope_type=scope_type, scope_id=scope_id)
    if role == "facilitator":
        Facilitation.objects.create(person=person, session=graph.session_a, role="lead")
    return user


EXPECTED = {
    None: set(), "participant": set(), "library_curator": {"manage_library"},
    "associate_trainer": {"view_sessions", "view_people"},
    "facilitator": {"view_sessions", "create_sessions", "manage_sessions", "import_attendance", "edit_records", "view_people", "nominate_trainers", "record_credentials", "view_dashboard"},
    "partner_coordinator": {"view_people", "view_dashboard", "export_reports"},
    "focal_person": {"view_sessions", "import_attendance", "edit_records", "view_people", "edit_people", "nominate_trainers", "review_credentials", "record_credentials", "view_dashboard", "export_reports"},
    "programme_owner": {"view_sessions", "create_sessions", "manage_sessions", "import_attendance", "edit_records", "view_people", "nominate_trainers", "approve_trainers", "manage_programmes", "review_credentials", "record_credentials", "view_dashboard", "export_reports"},
    "executive_viewer": {"view_dashboard", "export_reports"}, "super_admin": set(CAPABILITIES),
}


@pytest.mark.parametrize("role", list(EXPECTED))
def test_literal_capability_matrix(graph, role):
    user = actor(graph, role)
    expected = EXPECTED[role] | {"view_passport", "manage_own_passport"}
    for capability in CAPABILITIES:
        assert can(user, capability) == (capability in expected), (role, capability)
    assert not can(user, "unknown_capability")


def test_college_scope_includes_all_depths_not_siblings(graph):
    user = actor(graph, "focal_person")
    assert graph.unit_a.pk in unit_descendants(graph.college_a.pk)
    assert graph.unit_b.pk not in unit_descendants(graph.college_a.pk)
    assert can_view_person(user, graph.people["a"].pk)
    assert not can_view_person(user, graph.people["bridge"].pk)
    assert not can_view_person(user, graph.people["b"].pk)


def test_facilitator_programme_scope_is_not_all_programme_sessions(graph):
    user = actor(graph, "facilitator")
    assert set(session_queryset(user).values_list("pk", flat=True)) == {graph.session_a.pk}
    assert can_view_person(user, graph.people["bridge"].pk)
    assert not can_view_person(user, graph.people["b"].pk)


def test_shared_competency_does_not_grant_another_programmes_people_or_evidence(graph):
    user = actor(graph, "programme_owner")
    assert can_view_person(user, graph.people["a"].pk)
    assert not can_view_person(user, graph.people["b"].pk)
    evidence = scope_records(Evidence.objects.all(), user, "view_people")
    assert evidence.filter(person=graph.people["a"]).exists()
    assert not evidence.filter(person=graph.people["b"]).exists()


def test_scopes_from_multiple_roles_are_unioned_not_intersected(graph):
    user = actor(graph, "programme_owner")
    RoleAssignment.objects.create(user_account=user.hub_account, role="focal_person", scope_type="unit", scope_id=graph.college_b.pk)
    assert can_view_person(user, graph.people["a"].pk)
    assert can_view_person(user, graph.people["b"].pk)
    assert not can_view_person(user, graph.people["external_b"].pk)
    with reporting_scope(user):
        assert kpi_reach() == 4


@pytest.mark.parametrize("role,reach", [("facilitator", 3), ("programme_owner", 3), ("focal_person", 1), ("partner_coordinator", 1), ("executive_viewer", 5), ("super_admin", 5)])
def test_dashboard_role_scope_applies_to_all_time_default(client, graph, role, reach):
    user = actor(graph, role)
    force_login_with_mfa(client, user)
    response = client.get(reverse("dashboard"))
    assert response.status_code == 200
    expected = "<5" if role == "executive_viewer" and reach < 5 else reach
    assert response.context["reach"] == expected
    if role == "executive_viewer":
        assert not response.context["cascade_by_trainer"]


@pytest.mark.parametrize("role", ["facilitator", "focal_person", "programme_owner", "partner_coordinator"])
def test_foreign_report_filter_is_denied(client, graph, role):
    user = actor(graph, role)
    force_login_with_mfa(client, user)
    assert client.get(reverse("dashboard"), {"programme": graph.programme_b.pk}).status_code == 403


def test_facilitator_may_view_dashboard_but_not_export_or_generate_memo(client, graph):
    user = actor(graph, "facilitator")
    force_login_with_mfa(client, user)
    assert client.get(reverse("dashboard"), {"export": "csv"}).status_code == 403
    assert client.get(reverse("memo_generate")).status_code == 403
    assert not ExportLog.objects.exists()
    assert not Memo.objects.exists()


@pytest.mark.parametrize("role", [None, "participant", "library_curator", "executive_viewer", "partner_coordinator", "associate_trainer"])
def test_only_operational_editors_can_create_sessions_and_import(client, graph, role):
    user = actor(graph, role)
    force_login_with_mfa(client, user)
    assert client.post(reverse("session_create"), {}).status_code == 403
    assert client.get(reverse("import_upload")).status_code == 403


def test_reading_a_session_does_not_grant_closing_or_qr_rights(client, graph):
    user = actor(graph, "focal_person")
    force_login_with_mfa(client, user)
    assert client.get(reverse("session_console", args=[graph.session_a.pk])).status_code == 200
    assert client.get(reverse("session_qr_json", args=[graph.session_a.pk])).status_code == 403
    assert client.post(reverse("session_close", args=[graph.session_a.pk])).status_code == 403


def test_focal_roster_and_live_json_hide_other_colleges_in_same_session(client, graph):
    user = actor(graph, "focal_person")
    force_login_with_mfa(client, user)
    roster = client.get(reverse("session_roster_fragment", args=[graph.session_a.pk]))
    assert graph.people["a"].full_name.encode() in roster.content
    assert graph.people["bridge"].full_name.encode() not in roster.content
    live = client.get(reverse("session_live_stats", args=[graph.session_a.pk]))
    assert live.json()["count"] == 1


def test_facilitator_cannot_close_someone_elses_session_in_same_programme(client, graph):
    user = actor(graph, "facilitator")
    force_login_with_mfa(client, user)
    assert client.post(reverse("session_close", args=[graph.other_a.pk])).status_code == 404
    assert client.get(reverse("session_qr_json", args=[graph.other_a.pk])).status_code == 404


def test_executive_drilldown_contains_groups_not_individuals(client, graph):
    user = actor(graph, "executive_viewer")
    force_login_with_mfa(client, user)
    response = client.get(reverse("drilldown", args=["reach"]))
    assert response.status_code == 200
    for person in graph.people.values():
        assert person.full_name.encode() not in response.content
        assert person.email.encode() not in response.content
    assert client.get(reverse("person_list")).status_code == 403


def test_executive_plus_focal_role_only_gets_scoped_personal_drilldown(client, graph):
    user = actor(graph, "executive_viewer")
    RoleAssignment.objects.create(user_account=user.hub_account, role="focal_person", scope_type="unit", scope_id=graph.college_a.pk)
    force_login_with_mfa(client, user)
    response = client.get(reverse("drilldown", args=["reach"]))
    assert graph.people["a"].full_name.encode() in response.content
    assert graph.people["b"].full_name.encode() not in response.content
    assert graph.people["bridge"].full_name.encode() not in response.content


@pytest.mark.parametrize("role", [None, "participant", "facilitator", "focal_person", "programme_owner", "partner_coordinator", "executive_viewer", "library_curator"])
def test_django_staff_flag_cannot_grant_audit_or_dsr_access(client, graph, role):
    user = actor(graph, role, is_staff=True)
    force_login_with_mfa(client, user)
    assert client.get(reverse("audit_log")).status_code == 403
    assert client.post(reverse("dsr_erase", args=[graph.people["b"].pk])).status_code == 403
    graph.people["b"].refresh_from_db()
    assert graph.people["b"].is_anonymised is False


def test_curator_all_scope_cannot_leak_personal_data_even_with_django_permissions(client, graph):
    user = actor(graph, "library_curator", is_staff=True)
    user.user_permissions.set(Permission.objects.all())
    force_login_with_mfa(client, user)
    assert client.get(reverse("person_list")).status_code == 403
    assert client.get(reverse("admin:registry_person_changelist")).status_code == 403
    assert client.get(reverse("admin:auth_user_changelist")).status_code == 403
    assert client.get(reverse("admin:accounts_roleassignment_changelist")).status_code == 403
    assert client.get(reverse("library_dashboard")).status_code == 200


def test_non_staff_curator_can_use_library_draft_admin_without_other_admin_access(client, graph):
    user = actor(graph, "library_curator", is_staff=False)
    force_login_with_mfa(client, user)
    assert client.get(reverse("admin:library_crosswalkdraft_add")).status_code == 200
    assert client.get(reverse("admin:registry_person_changelist")).status_code == 403


def test_admin_querysets_and_foreignkey_choices_are_scoped(graph):
    user = actor(graph, "focal_person", is_staff=True)
    request = RequestFactory().get("/admin/")
    request.user = user
    model_admin = admin.site._registry[Person]
    assert model_admin.get_queryset(request).filter(pk=graph.people["a"].pk).exists()
    assert not model_admin.get_queryset(request).filter(pk=graph.people["b"].pk).exists()
    field = model_admin.formfield_for_foreignkey(Person._meta.get_field("unit"), request)
    assert field.queryset.filter(pk=graph.unit_a.pk).exists()
    assert not field.queryset.filter(pk=graph.unit_b.pk).exists()
    foreign = graph.people["b"]
    foreign.full_name = "ATTEMPTED OVERWRITE"
    with pytest.raises(PermissionDenied):
        model_admin.save_model(request, foreign, None, True)
    foreign.refresh_from_db()
    assert foreign.full_name == "PRIVATE PERSON b"


def test_owner_can_add_mapping_to_empty_owned_programme_but_not_foreign_parent(graph):
    user = actor(graph, "programme_owner")
    empty = Programme.objects.create(name="Empty owned programme")
    RoleAssignment.objects.create(user_account=user.hub_account, role="programme_owner", scope_type="programme", scope_id=empty.pk)
    request = RequestFactory().get("/admin/")
    request.user = user
    owner_admin = admin.site._registry[Programme]
    assert owner_admin.has_change_permission(request, empty)
    assert not owner_admin.has_change_permission(request, graph.programme_b)
    inline = next(i for i in owner_admin.get_inline_instances(request, empty) if i.model == ProgrammeCompetency)
    assert inline.has_add_permission(request, empty)
    assert not inline.has_add_permission(request, graph.programme_b)


@pytest.mark.parametrize("role,scope_type,scope_id", [("programme_owner", "all", None), ("library_curator", "unit", 1), ("participant", "own", 1), ("super_admin", "all", 1)])
def test_invalid_role_scope_is_rejected_and_legacy_rows_do_not_grant_access(graph, role, scope_type, scope_id):
    user = actor(graph)
    assignment = RoleAssignment(user_account=user.hub_account, role=role, scope_type=scope_type, scope_id=scope_id)
    with pytest.raises(ValidationError):
        assignment.full_clean()
    assignment.save()  # Simulate legacy data bypassing ModelForm validation.
    assert not can(user, "view_dashboard")
    assert not can(user, "view_people")


def test_report_context_does_not_leak_between_actors(graph):
    owner = actor(graph, "programme_owner")
    focal = actor(graph, "focal_person")
    with reporting_scope(owner):
        assert kpi_reach() == 3
        with reporting_scope(focal):
            assert kpi_reach() == 1
        assert kpi_reach() == 3
    assert kpi_reach() == 5


def test_no_role_user_cannot_claim_existing_person_with_unverified_email(client, graph):
    user = get_user_model().objects.create_user(username="unverified-claim", email=graph.people["b"].email)
    UserAccount.objects.create(user=user)
    with pytest.raises(PermissionDenied):
        resolve_person_for_user(user)
    client.force_login(user)
    assert client.get(reverse("my_passport")).status_code == 403
    assert user.hub_account.person_id is None


def test_verified_email_may_link_existing_unclaimed_person(graph):
    user = get_user_model().objects.create_user(username="verified-claim", email=graph.people["a"].email)
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    assert resolve_person_for_user(user).pk == graph.people["a"].pk


def test_anonymous_passport_link_is_read_only_and_signed_in_other_user_cannot_write(client, graph):
    url = issue_passport_link(graph.people["a"])
    token = url.rstrip("/").split("/")[-1]
    endpoint = reverse("passport_goals", args=[token])
    assert client.post(endpoint, {"title": "Unauthorized goal"}).status_code == 302
    user = actor(graph, "participant")
    client.force_login(user)
    assert client.post(endpoint, {"title": "Unauthorized goal"}).status_code == 403
    assert client.post(reverse("passport_profile_edit", args=[token]), {"full_name": "Overwrite"}).status_code == 403
    assert not LearningGoal.objects.filter(person=graph.people["a"]).exists()


def test_certificate_download_requires_own_or_scoped_access(client, graph, tmp_path):
    with override_settings(MEDIA_ROOT=tmp_path):
        credential = Credential.objects.create(person=graph.people["a"], kind="external", title="Private certificate", issued_at=timezone.localdate(), file=SimpleUploadedFile("private.pdf", b"%PDF-1.4\nprivate"))
        endpoint = reverse("credential_file", args=[credential.pk])
        assert client.get(endpoint).status_code == 302
        outsider = actor(graph, "partner_coordinator")
        client.force_login(outsider)
        assert client.get(endpoint).status_code == 404
        focal = actor(graph, "focal_person")
        client.force_login(focal)
        response = client.get(endpoint)
        assert response.status_code == 200
        assert "private" in response["Cache-Control"]
        assert b"private" in b"".join(response.streaming_content)


@pytest.mark.parametrize("tier,expected_role", [(2, None), (3, "associate_trainer"), (4, "facilitator")])
def test_promotion_grants_only_the_matching_programme_role(client, graph, tier, expected_role):
    owner = actor(graph, "programme_owner")
    status = TierStatus.objects.create(person=graph.people["a"], programme=graph.programme_a, tier=tier, status="nominated")
    nomination = Nomination.objects.create(person=status.person, programme=status.programme, tier=tier, nominated_by=owner)
    force_login_with_mfa(client, owner)
    response = client.post(reverse("nomination_detail", args=[nomination.pk]), {"decision": "approved", "comment": "Approved"})
    assert response.status_code == 302
    roles = RoleAssignment.objects.filter(user_account__person=status.person)
    if expected_role is None:
        assert not roles.exists()
    else:
        assert list(roles.values_list("role", "scope_type", "scope_id")) == [(expected_role, "programme", graph.programme_a.pk)]


def test_promotion_cannot_grant_access_to_an_account_for_another_person(client, graph):
    owner = actor(graph, "programme_owner")
    other_login = get_user_model().objects.create_user(username="wrong-account", email=graph.people["a"].email)
    UserAccount.objects.create(user=other_login, person=graph.people["b"])
    status = TierStatus.objects.create(person=graph.people["a"], programme=graph.programme_a, tier=3, status="nominated")
    nomination = Nomination.objects.create(person=status.person, programme=status.programme, tier=3, nominated_by=owner)
    force_login_with_mfa(client, owner)
    assert client.post(reverse("nomination_detail", args=[nomination.pk]), {"decision": "approved"}).status_code == 403
    nomination.refresh_from_db()
    assert nomination.decision == "pending"
    assert not other_login.hub_account.roles.exists()


def test_memos_are_scoped_and_legacy_or_revoked_scopes_do_not_leak(graph, monkeypatch):
    monkeypatch.setattr("apps.reporting.services.memo._generate_narrative", lambda *args: "Local narrative")
    owner = actor(graph, "programme_owner")
    focal = actor(graph, "focal_person")
    memo = generate_memo(created_by=owner, programme=graph.programme_a, period_label="Scope test")
    assert memo.figures["reach"] == 3
    assert memo.figures["cascade_top"] == []
    serialised = json.dumps(memo.figures)
    assert graph.people["b"].full_name not in serialised
    from apps.accounts.permissions import visible_memos
    assert visible_memos(owner).filter(pk=memo.pk).exists()
    assert not visible_memos(focal).filter(pk=memo.pk).exists()
    assert "executive_narrative" in memo.scope
    owner.hub_account.roles.all().delete()
    assert not visible_memos(owner).exists()
    executive = actor(graph, "executive_viewer")
    legacy = Memo.objects.create(created_by=executive, period_label="Legacy", figures={}, narrative="Private details")
    assert not visible_memos(executive).filter(pk=legacy.pk).exists()
