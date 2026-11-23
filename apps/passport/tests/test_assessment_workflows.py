import hashlib
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.library.models import EvidenceRule, Framework, Level
from apps.passport.assessment import (
    assessment_competencies,
    issue_assessment_link,
    submit_assessment,
)
from apps.passport.awards import issue_internal_award
from apps.passport.forms import SelfAssessmentForm
from apps.passport.models import (
    AssessmentLink,
    Credential,
    Evidence,
    InternalAssessment,
    SelfAssessment,
)
from apps.passport.services import verify_badge
from apps.registry.models import PassportToken
from apps.reporting.test_calculations import attend, session
from apps.reporting.test_calculations import graph as graph_fixture

graph = graph_fixture

from apps.sessions.models import SessionCompetency


def confirmed_link(g):
    raw = issue_assessment_link(g.person)
    link = AssessmentLink.objects.get(
        token_hash=hashlib.sha256(raw.encode()).hexdigest()
    )
    link.profile_confirmed_at = timezone.now()
    link.save()
    return raw, link


def test_sas_requires_valid_profile_not_raw_unit_ids(client, graph):
    raw = issue_assessment_link(graph.person)
    endpoint = reverse("sas_confirm", args=[raw])
    response = client.post(
        endpoint,
        {
            "full_name": "Updated name",
            "title": "Dr.",
            "category": "academic",
            "unit": "bad-unit",
            "phone": "",
            "age_band": "unknown",
            "consent": "on",
        },
    )
    assert response.status_code == 200
    graph.person.refresh_from_db()
    assert graph.person.full_name == "Math person"
    assert b"Select a valid choice" in response.content
    valid = {
        "full_name": "Updated name",
        "title": "Dr.",
        "category": "academic",
        "unit": str(graph.unit.pk),
        "phone": "",
        "age_band": "unknown",
        "consent": "on",
    }
    assert client.post(endpoint, valid).status_code == 302
    assert client.get(endpoint).status_code == 200


def test_sas_complete_persists_answers_correct_framework_and_prevents_replay(
    client, graph
):
    decoy = Framework.objects.create(code="DECOY", name="Decoy", is_knust=True)
    Level.objects.create(framework=decoy, ordinal=3, label="Decoy level")
    raw, _link = confirmed_link(graph)
    endpoint = reverse("sas_confirm", args=[raw])
    assert client.post(endpoint, {"consent": "on"}).status_code == 200
    assert not SelfAssessment.objects.exists()
    response = client.post(endpoint, {"consent": "on", f"comp_{graph.skill.pk}": "3"})
    assert response.status_code == 200
    assessment = SelfAssessment.objects.get()
    assert assessment.answers == {str(graph.skill.pk): 3}
    evidence = Evidence.objects.get(type="self")
    assert evidence.level_id == graph.levels[3].pk
    assert evidence.source_id == assessment.pk
    assert (
        client.post(
            endpoint, {"consent": "on", f"comp_{graph.skill.pk}": "5"}
        ).status_code
        == 400
    )
    assert SelfAssessment.objects.count() == 1


def test_sas_tokens_cannot_open_passports_and_expire(client, graph):
    raw, link = confirmed_link(graph)
    assert client.get(reverse("passport", args=[raw])).status_code == 404
    AssessmentLink.objects.filter(pk=link.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert client.get(reverse("sas_confirm", args=[raw])).status_code == 400
    PassportToken.objects.create(
        person=graph.person, token_hash=hashlib.sha256(b"passport-only").hexdigest()
    )
    assert client.get(reverse("sas_confirm", args=["passport-only"])).status_code == 400


@pytest.mark.parametrize("answer", ["bad", "0", "6", ""])
def test_sas_invalid_answer_is_atomic(graph, answer):
    _, link = confirmed_link(graph)
    form = SelfAssessmentForm(
        {"consent": "on", f"comp_{graph.skill.pk}": answer},
        competencies=assessment_competencies(),
    )
    with pytest.raises(ValidationError):
        submit_assessment(link.pk, form)
    assert not SelfAssessment.objects.exists()
    assert not Evidence.objects.exists()


def test_sas_unexpected_question_rejected(graph):
    _, _link = confirmed_link(graph)
    form = SelfAssessmentForm(
        {"consent": "on", f"comp_{graph.skill.pk}": "3", "comp_999999": "5"},
        competencies=assessment_competencies(),
    )
    assert not form.is_valid()


def award_context(g):
    workshop = session(g, "Assessed workshop")
    SessionCompetency.objects.create(session=workshop, competency=g.skill)
    attendance = attend(g.person, workshop)
    user = get_user_model().objects.create_user(username="award-owner")
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="programme_owner",
        scope_type="programme",
        scope_id=g.programme.pk,
    )
    EvidenceRule.objects.create(
        competency=g.skill, evidence_type="assessor", max_level=3
    )
    return user, attendance


@override_settings(DEBUG=True)
def test_internal_award_is_scoped_source_linked_and_idempotent(client, graph):
    user, attendance = award_context(graph)
    force_login_with_mfa(client, user)
    endpoint = reverse("internal_assessment", args=[attendance.pk])
    data = {
        "competency": graph.skill.pk,
        "level": graph.levels[3].pk,
        "evidence_type": "assessor",
        "note": "Observed participant task",
    }
    assert client.get(endpoint).status_code == 200
    assert client.post(endpoint, data).status_code == 302
    assert client.post(endpoint, data).status_code == 302
    award = InternalAssessment.objects.get()
    assert award.evidence.source_id == attendance.pk
    assert award.evidence.assessed_by_id == user.pk
    assert award.credential.kind == "knust_badge"
    assert verify_badge(award.credential.badge_json["jwt"])
    assert Credential.objects.count() == 1
    assert Evidence.objects.count() == 1


def test_internal_cap_partial_self_and_wrong_framework_blocked(graph):
    user, attendance = award_context(graph)
    values = {
        "competency": graph.skill,
        "level": graph.levels[5],
        "evidence_type": "assessor",
        "note": "Observed",
    }
    with pytest.raises(ValidationError):
        issue_internal_award(attendance.pk, user, **values)
    values["level"] = graph.levels[3]
    account = user.hub_account
    account.person = graph.person
    account.save()
    with pytest.raises(PermissionDenied):
        issue_internal_award(attendance.pk, user, **values)
    account.person = None
    account.save()
    attendance.status = "partial"
    attendance.check_out_at = None
    attendance.save()
    with pytest.raises(ValidationError):
        issue_internal_award(attendance.pk, user, **values)
    assert not Credential.objects.exists()


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_concurrent_internal_awards_share_one_source(graph):
    from concurrent.futures import ThreadPoolExecutor

    from django.db import close_old_connections, connection

    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL source locks")
    user, attendance = award_context(graph)

    def issue(_index):
        close_old_connections()
        try:
            return issue_internal_award(
                attendance.pk,
                user,
                competency=graph.skill,
                level=graph.levels[3],
                evidence_type="assessor",
                note="Concurrent assessment",
            )[1]
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = list(pool.map(issue, range(2)))
    assert sorted(changed) == [False, True]
    assert InternalAssessment.objects.count() == 1


def test_internal_cross_scope_and_wrong_framework_denied(graph):
    user, attendance = award_context(graph)
    outsider = get_user_model().objects.create_user(username="award-outsider")
    values = {
        "competency": graph.skill,
        "level": graph.levels[3],
        "evidence_type": "assessor",
        "note": "Observed",
    }
    with pytest.raises(PermissionDenied):
        issue_internal_award(attendance.pk, outsider, **values)
    foreign = Framework.objects.create(code="OTHER", name="Other level", is_knust=True)
    values["level"] = Level.objects.create(framework=foreign, ordinal=3, label="Other")
    with pytest.raises(ValidationError):
        issue_internal_award(attendance.pk, user, **values)
    assert not Credential.objects.exists()


def test_profile_requires_consent_and_minor_phone_is_rejected(client, graph):
    raw = issue_assessment_link(graph.person)
    endpoint = reverse("sas_confirm", args=[raw])
    data = {
        "full_name": graph.person.full_name,
        "title": "Dr.",
        "category": "academic",
        "unit": str(graph.unit.pk),
        "age_band": "under_18",
        "phone": "0241234567",
    }
    response = client.post(endpoint, data)
    assert response.status_code == 200
    assert b"This field is required" in response.content
    graph.person.refresh_from_db()
    assert graph.person.age_band == "unknown"
    assert not graph.person.consents.exists()
