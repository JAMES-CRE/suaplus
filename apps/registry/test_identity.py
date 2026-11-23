"""Canonical identity, ambiguity queues, privacy, and concurrent creation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.linking import resolve_person_for_user
from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.audit.models import AuditEvent
from apps.programmes.models import Programme
from apps.registry.identifiers import normalize_email, normalize_number, normalize_phone
from apps.registry.identity import (
    identity_data,
    match_identity,
    organisation_for_name,
    resolve_or_create,
)
from apps.registry.models import (
    IdentityConflict,
    IdentityMutex,
    Organisation,
    OrgUnit,
    Person,
)
from apps.registry.sync import sync_from_registry
from apps.registry.workflows import conflict_queryset, resolve_conflict
from apps.sessions.models import Attendance, Session


@pytest.fixture
def context(db):
    college = OrgUnit.objects.create(name="Identity college", type="college")
    unit = OrgUnit.objects.create(name="Identity department", type="department", parent=college)
    other_unit = OrgUnit.objects.create(name="Foreign department", type="department")
    organisation = Organisation.objects.create(name="Example School", type="senior_high_school")
    programme = Programme.objects.create(name="Identity programme")
    now = timezone.now()
    session = Session.objects.create(programme=programme, title="Identity workshop", start_at=now, end_at=now + timedelta(hours=2), mode="in_person", host_unit=unit, status="open")
    return SimpleNamespace(**locals())


def reviewer(context, role="super_admin"):
    number = get_user_model().objects.count()
    user = get_user_model().objects.create_user(username=f"identity-reviewer-{number}", email=f"reviewer{number}@knust.edu.gh")
    person = Person.objects.create(full_name="Identity reviewer", affiliation="staff", title="Dr.", category="academic", unit=context.unit)
    account = UserAccount.objects.create(user=user, person=person)
    kind, key = ("all", None) if role == "super_admin" else ("unit", context.college.pk) if role == "focal_person" else ("programme", context.programme.pk)
    RoleAssignment.objects.create(user_account=account, role=role, scope_type=kind, scope_id=key)
    return user


@pytest.mark.parametrize("value", ["024 123 4567", "0241234567", "+233 24 123 4567", "00233241234567", "233241234567", "241234567"])
def test_ghana_phone_forms_have_one_canonical_key(value):
    assert normalize_phone(value) == "+233241234567"


@pytest.mark.parametrize("value", ["phone me", "+0", "+233+241234567", "123", "11111111111111111111"])
def test_invalid_phones_are_not_identity_keys(value):
    with pytest.raises(ValidationError):
        normalize_phone(value)


def test_email_and_number_normalization():
    assert normalize_email("  Person@KNUST.EDU.GH ") == "person@knust.edu.gh"
    assert normalize_number(" st f001 ") == "STF001"


def test_matching_number_and_email_do_not_create_duplicates(context):
    person = Person.objects.create(full_name="Existing participant", affiliation="staff", staff_or_student_no="stf001", email="Person@KNUST.EDU.GH", unit=context.unit)
    result = resolve_or_create(identity_data(full_name="Existing participant", affiliation="staff", staff_or_student_no=" STF001 ", email=" person@knust.edu.gh "))
    assert result.person.pk == person.pk
    assert result.created is False
    assert Person.objects.count() == 1


def test_identifiers_for_different_people_queue_instead_of_overwriting(context):
    first = Person.objects.create(full_name="First person", affiliation="staff", staff_or_student_no="FIRST", email="first@knust.edu.gh")
    second = Person.objects.create(full_name="Second person", affiliation="staff", staff_or_student_no="SECOND", email="second@knust.edu.gh")
    result = resolve_or_create(identity_data(full_name="Submitted name", staff_or_student_no="FIRST", email="second@knust.edu.gh"), session=context.session)
    assert result.status == "conflict"
    assert result.person is None
    assert set(result.conflict.candidates.values_list("pk", flat=True)) == {first.pk, second.pk}
    assert not Attendance.objects.exists()
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.email == "first@knust.edu.gh"
    assert second.staff_or_student_no == "SECOND"


def test_shared_external_contact_needs_corroboration(context):
    first = Person.objects.create(full_name="Alice Example", affiliation="external", email="school@example.org", organisation=context.organisation)
    second = Person.objects.create(full_name="Bob Example", affiliation="external", email="school@example.org", organisation=context.organisation)
    ambiguous = match_identity(identity_data(email="school@example.org"))
    assert ambiguous.status == "conflict"
    known = match_identity(identity_data(full_name="bob   example", email="School@Example.org", organisation_id=context.organisation.pk))
    assert known.person.pk == second.pk
    assert known.person.pk != first.pk


def test_phone_formats_and_name_organisation_fallback_reuse_external_person(context):
    person = Person.objects.create(full_name="External Person", affiliation="external", phone="024 123 4567", organisation=context.organisation)
    match = match_identity(identity_data(full_name="external person", phone="+233241234567", organisation_id=context.organisation.pk))
    assert match.person.pk == person.pk
    fallback = match_identity(identity_data(full_name=" EXTERNAL   PERSON ", organisation_id=context.organisation.pk))
    assert fallback.person.pk == person.pk


def test_merged_identifiers_resolve_to_canonical_person_without_new_merge(context):
    canonical = Person.objects.create(full_name="Canonical person", affiliation="staff", email="current@knust.edu.gh")
    alias = Person.objects.create(full_name="Old person", affiliation="staff", email="old@knust.edu.gh", staff_or_student_no="OLD-ID", merged_into=canonical)
    match = match_identity(identity_data(staff_or_student_no="old-id", email="old@knust.edu.gh"))
    assert match.person.pk == canonical.pk
    alias.refresh_from_db()
    assert alias.merged_into_id == canonical.pk


def test_invalid_merge_chain_is_not_followed(context):
    first = Person.objects.create(full_name="Cycle first", affiliation="staff", email="cycle@knust.edu.gh")
    second = Person.objects.create(full_name="Cycle second", affiliation="staff", merged_into=first)
    first.merged_into = second
    first.save(update_fields=["merged_into"])
    result = match_identity(identity_data(email="cycle@knust.edu.gh"))
    assert result.status == "conflict"
    assert result.person is None


def test_anonymized_records_are_not_matched_or_resurrected(context):
    person = Person.objects.create(full_name="Anonymized", affiliation="staff", email="erased@knust.edu.gh", is_anonymised=True)
    assert person.identity_email == ""
    result = resolve_or_create(identity_data(email="erased@knust.edu.gh", full_name="Registry name"), source="registry")
    assert result.status == "error"
    assert Person.objects.count() == 1


def checkin_data(**updates):
    values = {"affiliation": "staff", "full_name": "Submitted participant", "email": "shared@knust.edu.gh", "phone": "", "age_band": "18_plus", "consent": "on"}
    return {**values, **updates}


def test_public_checkin_queues_ambiguity_without_disclosing_candidates(client, context):
    Person.objects.create(full_name="Private candidate one", affiliation="staff", email="shared@knust.edu.gh")
    Person.objects.create(full_name="Private candidate two", affiliation="staff", email="shared@knust.edu.gh")
    endpoint = reverse("checkin", args=[context.session.session_code])
    first = client.post(endpoint, checkin_data())
    second = client.post(endpoint, checkin_data())
    assert first.status_code == second.status_code == 200
    assert b"needs a quick review" in first.content
    assert b"Private candidate one" not in first.content
    assert b"Private candidate two" not in first.content
    assert IdentityConflict.objects.count() == 1
    assert Person.objects.count() == 2
    assert not Attendance.objects.exists()


def test_name_organisation_only_minor_requires_consent_and_stores_no_phone(client, context):
    data = checkin_data(affiliation="external", full_name="Minor participant", email="", organisation_name=" example   school ", age_band="under_18", guardian_name="Parent", guardian_contact="parent@example.org", guardian_consent="on")
    response = client.post(reverse("checkin", args=[context.session.session_code]), data)
    assert response.status_code == 302
    person = Person.objects.get(full_name="Minor participant")
    assert person.phone is None
    assert person.identity_phone == ""
    assert person.organisation_id == context.organisation.pk
    assert person.consents.filter(type="guardian").exists()
    assert Organisation.objects.count() == 1


def test_root_resolution_attaches_original_submission_once(context):
    first = Person.objects.create(full_name="Original person", affiliation="staff", email="shared@knust.edu.gh")
    Person.objects.create(full_name="Another person", affiliation="staff", email="shared@knust.edu.gh")
    stamp = timezone.now() - timedelta(minutes=2)
    result = resolve_or_create(identity_data(email="shared@knust.edu.gh", full_name="Unclear person", affiliation="staff", age_band="18_plus"), session=context.session, context={"consent": True, "submitted_at": stamp.isoformat()}, source="checkin")
    user = reviewer(context)
    conflict, attendance, created = resolve_conflict(result.conflict.pk, user, action="match", person_id=first.pk, note="Checked the participant's staff record")
    assert created is True
    assert conflict.status == "matched"
    assert attendance.person_id == first.pk
    assert attendance.check_in_at == stamp
    assert first.consents.get(type="data_processing").given_at == stamp
    with pytest.raises(ValidationError):
        resolve_conflict(conflict.pk, user, action="match", person_id=first.pk, note="Replay")
    assert Attendance.objects.count() == 1
    assert AuditEvent.objects.filter(action="identity_resolved").exists()


def test_confirming_distinct_external_person_does_not_merge_shared_contact(context):
    existing = Person.objects.create(full_name="Alice", affiliation="external", email="shared@example.org", organisation=context.organisation)
    submitted = identity_data(full_name="Bob", email="shared@example.org", affiliation="external", organisation_id=context.organisation.pk, age_band="18_plus")
    result = resolve_or_create(submitted, session=context.session, context={"consent": True}, source="checkin")
    root = reviewer(context)
    conflict, attendance, _created = resolve_conflict(result.conflict.pk, root, action="create", note="Separate people share a school email")
    assert conflict.selected_person_id != existing.pk
    assert attendance.person.full_name == "Bob"
    existing.refresh_from_db()
    assert existing.merged_into_id is None
    assert match_identity(submitted).person.pk == attendance.person_id


def test_focal_cannot_view_cross_college_candidates_even_for_hosted_workshop(client, context):
    Person.objects.create(full_name="Own candidate", affiliation="staff", email="shared@knust.edu.gh", unit=context.unit)
    foreign = Person.objects.create(full_name="FOREIGN PRIVATE PERSON", affiliation="staff", email="shared@knust.edu.gh", unit=context.other_unit)
    result = resolve_or_create(identity_data(email="shared@knust.edu.gh"), source="checkin", session=context.session, context={"consent": True})
    focal = reviewer(context, "focal_person")
    assert not conflict_queryset(focal).filter(pk=result.conflict.pk).exists()
    force_login_with_mfa(client, focal)
    assert client.get(reverse("identity_review_detail", args=[result.conflict.pk])).status_code == 404
    assert client.post(reverse("identity_review_detail", args=[result.conflict.pk]), {"action": "match", "person": foreign.pk, "note": "Attempt"}).status_code == 404


def test_owner_review_hides_unowned_candidate_details_and_cannot_select_them(client, context):
    foreign = Person.objects.create(full_name="Unowned private person", affiliation="external", email="shared@example.org", organisation=context.organisation)
    result = resolve_or_create(identity_data(full_name="Someone else", email="shared@example.org", affiliation="external", organisation_id=context.organisation.pk), session=context.session, context={"consent": True})
    owner = reviewer(context, "programme_owner")
    force_login_with_mfa(client, owner)
    response = client.get(reverse("identity_review_detail", args=[result.conflict.pk]))
    assert response.status_code == 200
    assert foreign.full_name.encode() not in response.content
    assert b"outside your data scope" in response.content
    with pytest.raises(PermissionDenied):
        resolve_conflict(result.conflict.pk, owner, action="match", person_id=foreign.pk, note="Attempt")


def test_registry_conflict_is_queued_without_overwriting_existing_keys(context):
    first = Person.objects.create(full_name="Registry first", affiliation="staff", staff_or_student_no="ID1", email="first@knust.edu.gh")
    second = Person.objects.create(full_name="Registry second", affiliation="staff", staff_or_student_no="ID2", email="second@knust.edu.gh")
    response = Mock()
    response.json.return_value = {"staff": [{"email": "second@knust.edu.gh", "id_number": "ID1", "full_name": "Wrong association", "phone": "0241234567"}], "students": []}
    result = sync_from_registry(api_url="https://registry.example/api", api_key="test-placeholder", http_get=Mock(return_value=response))
    assert result["conflicts"] == 1
    assert result["created"] == result["updated"] == 0
    assert IdentityConflict.objects.get().source == "registry"
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.email == "first@knust.edu.gh"
    assert second.staff_or_student_no == "ID2"
    assert first.phone is None


def test_verified_account_ambiguity_requires_review_not_passport_preference(context):
    Person.objects.create(full_name="First identity", affiliation="staff", email="login@knust.edu.gh")
    Person.objects.create(full_name="Second identity", affiliation="staff", email="login@knust.edu.gh")
    user = get_user_model().objects.create_user(username="identity-login", email="login@knust.edu.gh")
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    with pytest.raises(PermissionDenied):
        resolve_person_for_user(user)
    assert IdentityConflict.objects.get().source == "account"
    assert UserAccount.objects.get(user=user).person_id is None


def test_organisation_aliases_reuse_only_unique_names(context):
    organisation, candidates = organisation_for_name(" EXAMPLE   SCHOOL ")
    assert organisation.pk == context.organisation.pk
    assert candidates == ()
    duplicate = Organisation.objects.create(name="example school", type="basic_school")
    organisation, candidates = organisation_for_name("Example school")
    assert organisation is None
    assert set(candidates) == {context.organisation.pk, duplicate.pk}


@pytest.mark.parametrize("full_name,expected", [("", 200), ("Bob Example", 302)])
def test_shared_identifier_checkout_requires_disambiguation(client, context, full_name, expected):
    for name in ("Alice Example", "Bob Example"):
        person = Person.objects.create(full_name=name, affiliation="external", email="checkout@example.org", organisation=context.organisation)
        Attendance.objects.create(person=person, session=context.session, method="code", status="partial", check_in_at=timezone.now())
    response = client.post(reverse("checkout_identify", args=[context.session.session_code]), {"identifier": "checkout@example.org", "full_name": full_name})
    assert response.status_code == expected
    if full_name:
        bob = Person.objects.get(full_name="Bob Example")
        assert response.url == reverse("checkout", args=[context.session.session_code, bob.pk])
    else:
        assert b"Add your full name" in response.content


def test_review_page_matches_identity_and_replay_does_not_duplicate_attendance(client, context):
    first = Person.objects.create(full_name="First review person", affiliation="staff", email="review@knust.edu.gh")
    Person.objects.create(full_name="Second review person", affiliation="staff", email="review@knust.edu.gh")
    result = resolve_or_create(identity_data(email="review@knust.edu.gh"), source="checkin", session=context.session, context={"consent": True})
    root = reviewer(context)
    force_login_with_mfa(client, root)
    endpoint = reverse("identity_review_detail", args=[result.conflict.pk])
    response = client.post(endpoint, {"action": "match", "person": first.pk, "note": "Verified against the staff list"})
    assert response.status_code == 302
    result.conflict.refresh_from_db()
    assert result.conflict.status == "matched"
    assert Attendance.objects.get(session=context.session).person_id == first.pk
    replay = client.post(endpoint, {"action": "match", "person": first.pk, "note": "Replay"})
    assert replay.status_code == 200
    assert Attendance.objects.filter(session=context.session).count() == 1


def test_import_matches_staff_number_without_email_column(client, context, monkeypatch):
    person = Person.objects.create(full_name="Number-only staff", affiliation="staff", staff_or_student_no="STAFF-001", unit=context.unit)
    root = reviewer(context)
    force_login_with_mfa(client, root)
    state = client.session
    state["import_session_id"] = context.session.pk
    state["import_columns"] = ["Full name", "Staff number"]
    state["import_data"] = [{"Full name": "Number-only staff", "Staff number": " staff-001 "}]
    state.save()
    before = Person.objects.count()
    response = client.post(reverse("import_preview"), {"name_col": "Full name", "number_col": "Staff number"})
    assert response.status_code == 200
    assert response.context["matched_count"] == 1
    assert Person.objects.count() == before
    assert not Attendance.objects.exists()
    monkeypatch.setattr("apps.imports.views.send_passport_update", lambda *args: "participant")
    assert client.post(reverse("import_commit")).status_code == 302
    assert Attendance.objects.get(session=context.session).person_id == person.pk
    assert Person.objects.count() == before


def test_import_rechecks_identity_after_preview_and_queues_new_ambiguity(client, context):
    root = reviewer(context)
    force_login_with_mfa(client, root)
    state = client.session
    state["import_session_id"] = context.session.pk
    state["import_columns"] = ["Name", "Email"]
    state["import_data"] = [{"Name": "Unclear identity", "Email": "late@knust.edu.gh"}]
    state.save()
    response = client.post(reverse("import_preview"), {"name_col": "Name", "email_col": "Email"})
    assert response.context["new_count"] == 1
    Person.objects.create(full_name="Late first", affiliation="staff", email="late@knust.edu.gh")
    Person.objects.create(full_name="Late second", affiliation="staff", email="late@knust.edu.gh")
    assert client.post(reverse("import_commit")).status_code == 302
    assert not Attendance.objects.exists()
    conflict = IdentityConflict.objects.get(source="import")
    assert conflict.payload["context"]["status"] == "partial"
    assert conflict.candidates.count() == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_canonical_creators_share_one_person(context):
    if connection.vendor != "postgresql":
        pytest.skip("Production identity locking uses PostgreSQL")
    data = identity_data(full_name="Concurrent person", email="race@knust.edu.gh", staff_or_student_no="RACE-ID", affiliation="staff")

    def create():
        close_old_connections()
        try:
            with transaction.atomic():
                return resolve_or_create(data).person.pk
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _index: create(), range(2)))
    assert ids[0] == ids[1]
    assert Person.objects.filter(identity_number="RACE-ID").count() == 1
    assert IdentityMutex.objects.count() >= 1
