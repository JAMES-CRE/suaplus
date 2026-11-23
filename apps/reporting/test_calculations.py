"""Numerical regressions: chronology, denominators and distinct sources."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.core.models import AcademicPeriod
from apps.library.models import Competency, Domain, Framework, Level
from apps.passport.models import Credential, Evidence
from apps.programmes.models import Programme, ProgrammeCompetency, TierStatus
from apps.registry.models import OrgUnit, Person, UnitHeadcount
from apps.reporting.services.kpis import (
    kpi_cascade,
    kpi_cascade_by_trainer,
    kpi_cascade_top,
    kpi_coverage,
    kpi_coverage_heatmap,
    kpi_pipeline,
    kpi_pipeline_funnel,
    kpi_progression,
    kpi_progression_by_domain,
    kpi_workshops_held,
)
from apps.sessions.models import Attendance, Facilitation, Session


@pytest.fixture
def graph(db):
    college = OrgUnit.objects.create(name="Math college", type="college")
    unit = OrgUnit.objects.create(
        name="Math department", type="department", parent=college
    )
    framework = Framework.objects.create(code="MATH", name="Math", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="Math AI")
    skill = Competency.objects.create(
        framework=framework, domain=domain, code="AI-01", title="Math skill"
    )
    levels = {
        i: Level.objects.create(framework=framework, ordinal=i, label=str(i))
        for i in range(1, 6)
    }
    programme = Programme.objects.create(name="Math programme")
    ProgrammeCompetency.objects.create(
        programme=programme, competency=skill, target_level=3
    )
    person = Person.objects.create(
        full_name="Math person", affiliation="staff", category="academic", unit=unit
    )
    return SimpleNamespace(**locals())


def evidence(g, level, days, **kwargs):
    item = Evidence.objects.create(
        person=g.person,
        competency=g.skill,
        level=g.levels[level],
        type="external",
        source_type="credential",
        **kwargs,
    )
    Evidence.objects.filter(pk=item.pk).update(
        assessed_at=timezone.now() - timedelta(days=days)
    )
    return item


def session(g, name, days=1, status="closed"):
    start = timezone.now() - timedelta(days=days)
    return Session.objects.create(
        programme=g.programme,
        title=name,
        start_at=start,
        end_at=start + timedelta(hours=1),
        mode="in_person",
        status=status,
        host_unit=g.unit,
    )


def attend(person, workshop, status="completed"):
    return Attendance.objects.create(
        person=person,
        session=workshop,
        method="manual",
        status=status,
        check_in_at=workshop.start_at,
        check_out_at=workshop.end_at if status == "completed" else None,
    )


def test_progression_tracks_decline_not_level_range(graph):
    evidence(graph, 4, 10)
    evidence(graph, 5, 5)
    evidence(graph, 2, 1)
    assert kpi_progression(programme=graph.programme) == {
        "count": 1,
        "mean_delta": -2.0,
    }
    assert kpi_progression_by_domain(programme=graph.programme) == [
        {"domain": graph.domain.name, "mean_delta": -2.0, "people": 1}
    ]


def test_zero_change_counts_and_same_timestamp_is_not_progression(graph):
    first = evidence(graph, 3, 10)
    second = evidence(graph, 3, 1)
    assert kpi_progression()["count"] == 1
    assert kpi_progression()["mean_delta"] == 0
    first.refresh_from_db()
    Evidence.objects.filter(pk=second.pk).update(assessed_at=first.assessed_at)
    assert kpi_progression()["count"] == 0


def test_expired_and_rejected_sources_do_not_count_before_sweep(graph):
    evidence(graph, 3, 1, expires_at=timezone.localdate() - timedelta(days=1))
    credential = Credential.objects.create(
        person=graph.person,
        kind="external",
        source="self_submitted",
        title="Rejected",
        issued_at=timezone.localdate(),
        review_status="rejected",
    )
    evidence(graph, 5, 0, credential=credential)
    assert kpi_coverage()["numerator"] == 0
    assert kpi_pipeline_funnel()["with_evidence"] == 0


def test_headcount_latest_snapshot_and_parent_rollups(graph):
    today = timezone.localdate()
    old = AcademicPeriod.objects.create(
        year_start=2024,
        year_end=2025,
        semester=1,
        start_date=today - timedelta(days=500),
        end_date=today - timedelta(days=300),
    )
    current = AcademicPeriod.objects.create(
        year_start=2025,
        year_end=2026,
        semester=1,
        start_date=today - timedelta(days=50),
        end_date=today + timedelta(days=50),
    )
    UnitHeadcount.objects.create(
        unit=graph.unit, category="academic", period=old, headcount=500
    )
    UnitHeadcount.objects.create(
        unit=graph.college, category="academic", period=current, headcount=20
    )
    UnitHeadcount.objects.create(
        unit=graph.unit, category="academic", period=current, headcount=10
    )
    UnitHeadcount.objects.create(
        unit=graph.unit, category="non_academic", period=current, headcount=5
    )
    UnitHeadcount.objects.create(
        unit=graph.unit, category="undergrad", period=current, headcount=50
    )
    evidence(graph, 3, 1)
    assert kpi_coverage(unit=graph.college, affiliation="staff") == {
        "numerator": 1,
        "denominator": 25,
        "percent": 4.0,
    }
    assert kpi_coverage(unit=graph.unit, category="academic")["denominator"] == 10
    assert kpi_coverage(affiliation="external")["percent"] is None
    assert (
        kpi_coverage_heatmap(unit=graph.college, affiliation="staff")[0]["total"] == 25
    )


def test_pipeline_counts_people_once_at_highest_approved_tier(graph):
    other = Programme.objects.create(name="Other math")
    for programme, tier in [(graph.programme, 3), (graph.programme, 4), (other, 5)]:
        TierStatus.objects.create(
            person=graph.person, programme=programme, tier=tier, status="approved"
        )
    assert kpi_pipeline() == {
        "Explorer": 0,
        "Practitioner": 0,
        "Associate": 0,
        "Certified": 0,
        "Master": 1,
    }
    funnel = kpi_pipeline_funnel()
    assert funnel["approved"] == funnel["certified"] == funnel["master"] == 1


def test_cascade_completion_and_chronological_second_generation(graph):
    trainee = Person.objects.create(full_name="Trainee", affiliation="staff")
    downstream = Person.objects.create(full_name="Downstream", affiliation="staff")
    early = session(graph, "Too early", days=5)
    direct = session(graph, "Direct", days=3)
    later = session(graph, "Later", days=1)
    Facilitation.objects.create(person=graph.person, session=direct, role="lead")
    for workshop in [early, later]:
        Facilitation.objects.create(person=trainee, session=workshop, role="lead")
        attend(downstream, workshop)
    attend(trainee, direct)
    partial = Person.objects.create(full_name="Partial trainee", affiliation="staff")
    attend(partial, direct, "partial")
    rows = kpi_cascade_by_trainer(programme=graph.programme)
    root = next(row for row in rows if row["trainer"] == graph.person.full_name)
    assert root == {
        "trainer": graph.person.full_name,
        "direct": 1,
        "second_generation": 1,
        "multiplier": 2.0,
    }
    assert kpi_cascade(programme=graph.programme)["mean_multiplier"] == 1.5
    assert kpi_cascade_top()[0]["second_gen"] == 1
    later.status = "cancelled"
    later.save()
    assert (
        next(
            row
            for row in kpi_cascade_by_trainer()
            if row["trainer"] == graph.person.full_name
        )["second_generation"]
        == 0
    )


def test_workshops_excludes_future_cancelled_and_scheduled(graph):
    session(graph, "Delivered")
    session(graph, "Future", days=-1)
    session(graph, "Cancelled", status="cancelled")
    session(graph, "Scheduled", status="scheduled")
    assert kpi_workshops_held(unit=graph.college) == 1


def test_csat_excludes_partial_cancelled_and_withdrawn_records(graph):
    from apps.library.models import SurveyTemplate
    from apps.reporting.services.kpis import (
        kpi_csat,
        kpi_csat_distribution,
        kpi_csat_trend,
    )
    from apps.sessions.models import SurveyResponse

    template = SurveyTemplate.objects.create(
        name="Math exit", type="exit", definition={}
    )
    for name, status, score in [
        ("Complete", "completed", 5),
        ("Partial", "partial", 1),
        ("Withdrawn", "withdrawn", 1),
    ]:
        workshop = session(graph, name)
        row = attend(graph.person, workshop, status)
        SurveyResponse.objects.create(
            attendance=row, template=template, type="exit", csat=score
        )
    cancelled = session(graph, "Cancelled responses", status="cancelled")
    SurveyResponse.objects.create(
        attendance=attend(graph.person, cancelled),
        template=template,
        type="exit",
        csat=1,
    )
    assert kpi_csat() == {"mean": 5.0, "count": 1}
    assert kpi_csat_distribution()["total"] == 1
    assert kpi_csat_trend()[0]["mean"] == 5.0
