"""Completion evidence, mapping history, and replay regressions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.forms.models import inlineformset_factory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.audit.models import AuditEvent
from apps.library.models import (
    Competency,
    Domain,
    EvidenceRule,
    Framework,
    Level,
    SurveyTemplate,
)
from apps.passport.models import Evidence
from apps.programmes.models import Programme, ProgrammeCompetency
from apps.registry.models import PassportToken, Person
from apps.sessions.admin import SessionCompetencyFormSet
from apps.sessions.models import (
    Attendance,
    Session,
    SessionCompetency,
    SurveyResponse,
)
from apps.sessions.services import (
    complete_attendance,
    grant_attendance_evidence,
    set_session_competencies,
)


@pytest.fixture
def training(db):
    decoy = Framework.objects.create(code="DECOY", name="Another KNUST framework", is_knust=True)
    Level.objects.create(framework=decoy, ordinal=1, label="Aware")
    framework = Framework.objects.create(code="TRAIN", name="Training framework", is_knust=True)
    aware = Level.objects.create(framework=framework, ordinal=1, label="Aware")
    proficient = Level.objects.create(framework=framework, ordinal=3, label="Proficient")
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    programme = Programme.objects.create(name="Completion programme")
    skills = []
    for number in (1, 2):
        skill = Competency.objects.create(framework=framework, domain=domain, code=f"AI-0{number}", title=f"Skill {number}")
        ProgrammeCompetency.objects.create(programme=programme, competency=skill, target_level=3)
        skills.append(skill)
    now = timezone.now()
    session = Session.objects.create(programme=programme, title="Completion workshop", start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1), mode="in_person", status="open")
    SessionCompetency.objects.create(session=session, competency=skills[0])
    person = Person.objects.create(affiliation="staff", full_name="Completion participant", email="participant@knust.edu.gh", title="Dr.", category="academic", unit_other="KEC")
    attendance = Attendance.objects.create(person=person, session=session, method="code", status="partial", check_in_at=now - timedelta(minutes=20))
    SurveyTemplate.objects.create(name="Exit completion survey", type="exit", definition={}, is_default=True)
    return SimpleNamespace(**locals())


@pytest.fixture
def owner(training):
    user = get_user_model().objects.create_user(username="completion-owner", email="owner@knust.edu.gh")
    person = Person.objects.create(affiliation="staff", full_name="Completion owner", title="Dr.", category="academic", unit_other="KEC")
    account = UserAccount.objects.create(user=user, person=person)
    RoleAssignment.objects.create(user_account=account, role="programme_owner", scope_type="programme", scope_id=training.programme.pk)
    return user


def test_partial_attendance_does_not_grant_a_skill(training):
    assert grant_attendance_evidence(training.attendance) == 0
    assert not Evidence.objects.exists()


def test_completion_records_matching_framework_level_one_and_source(training):
    attendance, changed, count = complete_attendance(training.attendance.pk)
    assert changed is True
    assert count == 1
    evidence = Evidence.objects.get(type="attendance")
    assert evidence.source_id == attendance.pk
    assert evidence.person_id == attendance.person_id
    assert evidence.session_id == attendance.session_id
    assert evidence.level_id == training.aware.pk
    assert evidence.assessed_at == attendance.check_out_at
    assert evidence.framework_version.startswith("TRAIN:")


def test_attendance_rule_cannot_raise_attendance_above_level_one(training):
    EvidenceRule.objects.create(evidence_type="attendance", max_level=5, status="active")
    complete_attendance(training.attendance.pk)
    assert Evidence.objects.get().level_id == training.aware.pk


def test_completion_replay_keeps_feedback_timestamp_and_passport_link(client, training, monkeypatch):
    calls = []
    from apps.sessions.services import send_passport_update
    monkeypatch.setattr("apps.notifications.services.send_passport_link_to_participant", lambda **kwargs: [])

    def delivered(person, title):
        calls.append(person.pk)
        return send_passport_update(person, title)

    monkeypatch.setattr("apps.sessions.views.send_passport_update", delivered)
    endpoint = reverse("checkout", args=[training.session.session_code, training.person.pk])
    first = client.post(endpoint, {"csat": 5, "what_will_you_apply": "Apply the training", "interested_in_training": "on"})
    assert first.status_code == 200
    training.attendance.refresh_from_db()
    completed_at = training.attendance.check_out_at
    token = PassportToken.objects.get(person=training.person)
    second = client.post(endpoint, {"csat": 1, "what_will_you_apply": "Overwrite"})
    assert second.status_code == 200
    assert second.context["already_completed"] is True
    response = SurveyResponse.objects.get(attendance=training.attendance, type="exit")
    assert response.csat == 5
    assert response.answers["what_will_you_apply"] == "Apply the training"
    assert response.interested_in_training is True
    training.attendance.refresh_from_db()
    assert training.attendance.check_out_at == completed_at
    assert Evidence.objects.count() == 1
    assert calls == [training.person.pk]
    token.refresh_from_db()
    assert token.revoked_at is None
    assert PassportToken.objects.filter(person=training.person).count() == 1


def test_invalid_checkout_leaves_all_records_unchanged(client, training):
    response = client.post(reverse("checkout", args=[training.session.session_code, training.person.pk]), {"csat": 6})
    assert response.status_code == 200
    training.attendance.refresh_from_db()
    assert training.attendance.status == "partial"
    assert not Evidence.objects.exists()
    assert not SurveyResponse.objects.exists()
    assert not PassportToken.objects.exists()


@pytest.mark.parametrize("status", ["absent", "withdrawn"])
def test_unchecked_in_records_cannot_be_confirmed(training, status):
    Attendance.objects.filter(pk=training.attendance.pk).update(status=status, check_in_at=None)
    with pytest.raises(ValidationError):
        complete_attendance(training.attendance.pk)
    assert not Evidence.objects.exists()


def test_completion_rolls_back_survey_and_attendance_if_evidence_fails(training, monkeypatch):
    def fail(_attendance):
        raise ValidationError("Evidence could not be recorded")
    monkeypatch.setattr("apps.sessions.services.grant_attendance_evidence", fail)
    with pytest.raises(ValidationError):
        complete_attendance(training.attendance.pk, checkout_data={"csat": 4, "what_will_you_apply": "Practice", "interested_in_training": False})
    training.attendance.refresh_from_db()
    assert training.attendance.status == "partial"
    assert training.attendance.check_out_at is None
    assert not SurveyResponse.objects.exists()


def test_database_rejects_duplicate_source_claims_and_valid_claims_without_source(training):
    complete_attendance(training.attendance.pk)
    values = {"person": training.person, "session": training.session, "competency": training.skills[0], "level": training.aware, "type": "attendance", "source_type": "attendance"}
    with pytest.raises(IntegrityError), transaction.atomic():
        Evidence.objects.create(**values, source_id=training.attendance.pk)
    with pytest.raises(IntegrityError), transaction.atomic():
        Evidence.objects.create(**values)


def test_evidence_form_validation_rejects_high_attendance_level(training):
    complete_attendance(training.attendance.pk)
    evidence = Evidence.objects.get()
    evidence.level = training.proficient
    with pytest.raises(ValidationError):
        evidence.full_clean()


def test_owner_confirmation_creates_evidence_and_audit_once(client, training, owner, monkeypatch):
    calls = []
    monkeypatch.setattr("apps.sessions.views.send_passport_update", lambda *args: calls.append(args) or "participant")
    force_login_with_mfa(client, owner)
    endpoint = reverse("attendance_confirm", args=[training.session.pk, training.attendance.pk])
    assert client.post(endpoint, {"reason": "Observed full participation"}).status_code == 302
    assert client.post(endpoint, {"reason": "Replay"}).status_code == 302
    assert Evidence.objects.count() == 1
    assert len(calls) == 1
    event = AuditEvent.objects.get(action="attendance_confirmed")
    assert event.actor_id == owner.pk
    assert event.after["reason"] == "Observed full participation"
    assert not SurveyResponse.objects.exists()


def test_confirmation_requires_reason_and_authorised_scope(client, training, owner):
    force_login_with_mfa(client, owner)
    endpoint = reverse("attendance_confirm", args=[training.session.pk, training.attendance.pk])
    assert client.get(endpoint).status_code == 405
    assert client.post(endpoint, {}).status_code == 302
    training.attendance.refresh_from_db()
    assert training.attendance.status == "partial"
    other = Programme.objects.create(name="Foreign confirmation programme")
    owner.hub_account.roles.update(scope_id=other.pk)
    assert client.post(endpoint, {"reason": "Unauthorized"}).status_code == 404
    assert not Evidence.objects.exists()


def test_session_mapping_changes_are_locked_after_checkin(training):
    with pytest.raises(ValidationError):
        set_session_competencies(training.session, [training.skills[1]])
    assert list(training.session.competencies.values_list("competency_id", flat=True)) == [training.skills[0].pk]


def test_admin_inline_cannot_delete_a_frozen_mapping(training):
    formset_class = inlineformset_factory(Session, SessionCompetency, fields=("competency",), formset=SessionCompetencyFormSet, extra=0, can_delete=True)
    coverage = training.session.competencies.get()
    data = {"competencies-TOTAL_FORMS": "1", "competencies-INITIAL_FORMS": "1", "competencies-MIN_NUM_FORMS": "0", "competencies-MAX_NUM_FORMS": "1000", "competencies-0-id": str(coverage.pk), "competencies-0-session": str(training.session.pk), "competencies-0-competency": str(coverage.competency_id), "competencies-0-DELETE": "on"}
    formset = formset_class(data, instance=training.session, prefix="competencies")
    assert not formset.is_valid()
    assert "locked" in str(formset.non_form_errors())


def creation_data(training, **updates):
    values = {"programme": str(training.programme.pk), "title": "New mapped workshop", "mode": "in_person", "venue": "Room", "start_date": "2026-11-20", "start_time": "10:00", "end_date": "2026-11-20", "end_time": "12:00", "competencies": [str(training.skills[0].pk), str(training.skills[1].pk)]}
    return {**values, **updates}


def test_session_creation_saves_mappings_lead_and_aware_times(client, training, owner):
    force_login_with_mfa(client, owner)
    response = client.post(reverse("session_create"), creation_data(training))
    assert response.status_code == 302
    session = Session.objects.get(title="New mapped workshop")
    assert set(session.competencies.values_list("competency_id", flat=True)) == {skill.pk for skill in training.skills}
    assert timezone.is_aware(session.start_at)
    assert session.facilitators.get().person_id == owner.hub_account.person_id


def test_creation_rejects_unmapped_competencies_and_missing_mappings(client, training, owner):
    force_login_with_mfa(client, owner)
    unknown = Competency.objects.create(framework=training.framework, domain=training.domain, code="OTHER", title="Unmapped skill")
    for skills in ([], [str(unknown.pk)]):
        response = client.post(reverse("session_create"), creation_data(training, competencies=skills))
        assert response.status_code == 200
        assert response.context["form"].errors.get("competencies")
    assert not Session.objects.filter(title="New mapped workshop").exists()


def test_edit_keeps_frozen_mapping_even_if_another_id_is_posted(client, training, owner):
    force_login_with_mfa(client, owner)
    response = client.post(reverse("session_edit", args=[training.session.pk]), creation_data(training, title="Corrected title", competencies=[str(training.skills[1].pk)]))
    assert response.status_code == 302
    training.session.refresh_from_db()
    assert training.session.title == "Corrected title"
    assert list(training.session.competencies.values_list("competency_id", flat=True)) == [training.skills[0].pk]


def test_closed_session_import_grants_evidence_but_open_import_does_not(client, training, owner, monkeypatch):
    monkeypatch.setattr("apps.imports.views.send_passport_update", lambda *args: "participant")
    force_login_with_mfa(client, owner)
    for status, email in [("open", "open-import@example.org"), ("closed", "closed-import@example.org")]:
        training.session.status = status
        training.session.save(update_fields=["status"])
        state = client.session
        state["import_session_id"] = training.session.pk
        state["import_preview"] = [{"email": email, "name": "Imported participant", "status": "new", "note": ""}]
        state.save()
        assert client.post(reverse("import_commit")).status_code == 302
        attendance = Attendance.objects.get(person__email=email, session=training.session)
        evidence = Evidence.objects.filter(source_id=attendance.pk, type="attendance")
        assert evidence.exists() == (status == "closed")
        if status == "closed":
            assert evidence.get().assessed_at == training.session.end_at


@pytest.mark.django_db(transaction=True)
def test_concurrent_completion_has_one_survey_and_one_source_claim(training):
    if connection.vendor != "postgresql":
        pytest.skip("The production locking contract uses PostgreSQL")
    attendance_id = training.attendance.pk
    data = {"csat": 4, "what_will_you_apply": "Practice", "interested_in_training": False}

    def complete():
        close_old_connections()
        try:
            return complete_attendance(attendance_id, checkout_data=data)[1]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _value: complete(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert Evidence.objects.filter(source_id=attendance_id).count() == 1
    assert SurveyResponse.objects.filter(attendance_id=attendance_id, type="exit").count() == 1
