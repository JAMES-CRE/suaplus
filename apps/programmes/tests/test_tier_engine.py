import pytest

from apps.library.models import Competency, Domain, Framework, Level
from apps.passport.models import Evidence
from apps.programmes.models import (
    Programme,
    ProgrammeCompetency,
    TierCriterion,
    TierStatus,
)
from apps.programmes.services.tier_engine import (
    compute_tier_status,
    evaluate_tier,
)
from apps.registry.models import Person


@pytest.fixture
def framework(db):
    f = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    for i, label in [(1, "Aware"), (2, "Developing"), (3, "Proficient")]:
        Level.objects.create(framework=f, ordinal=i, label=label)
    return f


@pytest.fixture
def domain(framework):
    return Domain.objects.create(framework=framework, code="AI", name="AI")


@pytest.fixture
def competency(domain):
    return Competency.objects.create(
        framework=domain.framework, domain=domain, code="AI-01", title="AI basics"
    )


@pytest.fixture
def programme(db):
    p = Programme.objects.create(name="Test Prog")
    return p


@pytest.fixture
def person(db):
    return Person.objects.create(
        affiliation="staff", full_name="Kwame Mensah", email="km@knust.edu.gh"
    )


@pytest.mark.django_db
def test_practitioner_criterion_not_met(person, programme, competency, framework):
    TierCriterion.objects.create(
        programme=programme,
        tier=2,
        rule_type="min_level",
        parameters={"competency": "AI-01", "level": 2},
    )
    ProgrammeCompetency.objects.create(
        programme=programme, competency=competency, target_level=2
    )
    evaluation = evaluate_tier(person, programme, tier=2)
    assert evaluation.criteria_total == 1
    assert evaluation.criteria_met == 0
    assert not evaluation.all_met


@pytest.mark.django_db
def test_practitioner_criterion_met(person, programme, competency, framework):
    level2 = Level.objects.get(framework=framework, ordinal=2)
    Evidence.objects.create(
        person=person,
        competency=competency,
        level=level2,
        type="external",
        source_type="credential",
    )
    TierCriterion.objects.create(
        programme=programme,
        tier=2,
        rule_type="min_level",
        parameters={"competency": "AI-01", "level": 2},
    )
    evaluation = evaluate_tier(person, programme, tier=2)
    assert evaluation.criteria_total == 1
    assert evaluation.criteria_met == 1
    assert evaluation.all_met


@pytest.mark.django_db
def test_self_evidence_excluded(person, programme, competency, framework):
    level3 = Level.objects.get(framework=framework, ordinal=3)
    Evidence.objects.create(
        person=person,
        competency=competency,
        level=level3,
        type="self",
        source_type="self_assessment",
    )
    TierCriterion.objects.create(
        programme=programme,
        tier=2,
        rule_type="min_level",
        parameters={"competency": "AI-01", "level": 2},
    )
    evaluation = evaluate_tier(person, programme, tier=2)
    assert evaluation.criteria_met == 0  # self-reported doesn't count


@pytest.mark.django_db
def test_compute_writes_tier_status(person, programme, competency, framework):
    level2 = Level.objects.get(framework=framework, ordinal=2)
    Evidence.objects.create(
        person=person,
        competency=competency,
        level=level2,
        type="external",
        source_type="credential",
    )
    ProgrammeCompetency.objects.create(
        programme=programme, competency=competency, target_level=2
    )
    TierCriterion.objects.create(
        programme=programme,
        tier=2,
        rule_type="min_level",
        parameters={"competency": "AI-01", "level": 2},
    )
    summary = compute_tier_status(programme)
    assert summary["statuses_written"] == 1
    ts = TierStatus.objects.get(person=person, programme=programme, tier=2)
    assert ts.criteria_met == 1
    assert ts.criteria_total == 1