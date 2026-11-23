"""Tier activity must be valid, completed and programme-specific."""

from datetime import timedelta

from django.db import IntegrityError
from django.utils import timezone

from apps.library.models import (
    CertificationCatalogue,
    CertificationCompetency,
    SurveyTemplate,
)
from apps.passport.models import Credential, Evidence
from apps.programmes.models import Programme, TierCriterion, TierStatus
from apps.programmes.services import tier_engine
from apps.reporting.test_calculations import attend, evidence, session
from apps.reporting.test_calculations import graph as graph_fixture

graph = graph_fixture

from apps.sessions.models import Facilitation, SurveyResponse


def criterion(g, rule, params):
    return TierCriterion.objects.create(
        programme=g.programme, tier=3, rule_type=rule, parameters=params
    )


def test_min_level_excludes_expired_and_wrong_programme(graph):
    criterion(graph, "min_level", {"competency": graph.skill.code, "level": 2})
    evidence(graph, 4, 1, expires_at=timezone.localdate() - timedelta(days=1))
    assert not tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met
    evidence(graph, 3, 1)
    assert tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met
    other = Programme.objects.create(name="Wrong tier programme")
    TierCriterion.objects.create(
        programme=other,
        tier=3,
        rule_type="min_level",
        parameters={"competency": graph.skill.code, "level": 2},
    )
    assert not tier_engine.evaluate_tier(graph.person, other, 3).all_met


def test_completed_sessions_and_csat_only(graph):
    criterion(graph, "min_sessions", {"sessions": 1})
    criterion(graph, "min_mean_csat", {"threshold": 4})
    workshop = session(graph, "Delivered tier")
    Facilitation.objects.create(person=graph.person, session=workshop, role="lead")
    attendance = attend(graph.person, workshop)
    template = SurveyTemplate.objects.create(
        name="Tier exit", type="exit", definition={}
    )
    SurveyResponse.objects.create(
        attendance=attendance, template=template, type="exit", csat=5
    )
    assert tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met
    workshop.status = "open"
    workshop.save()
    assert tier_engine.evaluate_tier(graph.person, graph.programme, 3).criteria_met == 0


def test_recency_dormancy_reactivation_preserve_approval(graph):
    criterion(graph, "recency_window", {"months": 12})
    status = TierStatus.objects.create(
        person=graph.person,
        programme=graph.programme,
        tier=3,
        status="approved",
        approved_at=timezone.now(),
    )
    other = Programme.objects.create(name="Other activity")
    workshop = session(graph, "Foreign recent")
    workshop.programme = other
    workshop.save()
    attend(graph.person, workshop)
    tier_engine.compute_tier_status(graph.programme)
    status.refresh_from_db()
    assert status.status == "dormant"
    approved_at = status.approved_at
    evidence(graph, 3, 1)
    tier_engine.compute_tier_status(graph.programme)
    status.refresh_from_db()
    assert status.status == "approved"
    assert status.approved_at == approved_at


def test_pending_nomination_and_unknown_rule(graph):
    criterion(graph, "unknown", {})
    TierStatus.objects.create(
        person=graph.person, programme=graph.programme, tier=3, status="nominated"
    )
    evidence(graph, 3, 1)
    tier_engine.compute_tier_status()
    assert TierStatus.objects.get(person=graph.person).status == "nominated"
    assert not tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met


def test_credential_requires_current_programme_mapping(graph):
    criterion(graph, "required_credential", {"title": "Sanctioned"})
    catalogue = CertificationCatalogue.objects.create(issuer="Test", title="Sanctioned")
    credential = Credential.objects.create(
        person=graph.person,
        kind="external",
        source="staff_recorded",
        title="Sanctioned",
        issuer="Test",
        catalogue=catalogue,
        issued_at=timezone.localdate(),
        review_status="approved",
    )
    assert not tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met
    CertificationCompetency.objects.create(
        certification=catalogue, competency=graph.skill, max_level=3
    )
    assert tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met
    credential.expires_at = timezone.localdate() - timedelta(days=1)
    credential.save()
    assert not tier_engine.evaluate_tier(graph.person, graph.programme, 3).all_met


def test_failed_evaluation_savepoint_does_not_abort_next_person(graph, monkeypatch):
    from apps.registry.models import Person

    criterion(graph, "min_level", {"competency": graph.skill.code, "level": 2})
    evidence(graph, 3, 1)
    other = Person.objects.create(full_name="Next tier person", affiliation="staff")
    Evidence.objects.create(
        person=other,
        competency=graph.skill,
        level=graph.levels[3],
        type="external",
        source_type="credential",
    )
    original = tier_engine._write_tier_status

    def failing(evaluation, programme, tier):
        if evaluation.person_id == graph.person.pk:
            raise IntegrityError("Injected failure")
        return original(evaluation, programme, tier)

    monkeypatch.setattr(tier_engine, "_write_tier_status", failing)
    result = tier_engine.compute_tier_status(graph.programme)
    assert result["errors"] == 1
    assert result["statuses_written"] == 1
    assert TierStatus.objects.filter(person=other).exists()
