"""KPI computations - pure functions that aggregate from the database.

Each function takes optional filters and returns a scalar or a list.
The dashboard view calls these to build the tiles.

All functions exclude self-reported evidence from progression/coverage -
that matches the spec's rule that self-ratings never count toward KPIs.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone

from apps.passport.models import Evidence
from apps.programmes.models import Programme
from apps.registry.models import Person, UnitHeadcount
from apps.sessions.models import Attendance, Facilitation, Session, SurveyResponse


def suppress(count, threshold=5):
    if count is None:
        return "-"
    return "<5" if count < threshold else count


def _attendance_qs(programme=None, unit=None, since=None, until=None):
    """Base queryset of completed/partial attendances, with optional filters."""
    qs = Attendance.objects.filter(status__in=["completed", "partial"])
    if programme:
        qs = qs.filter(session__programme=programme)
    if unit:
        qs = qs.filter(person__unit=unit)
    if since:
        qs = qs.filter(check_in_at__gte=since)
    if until:
        qs = qs.filter(check_in_at__lte=until)
    return qs


def kpi_reach(programme=None, unit=None, since=None, until=None):
    """Distinct people trained in the period."""
    qs = _attendance_qs(programme, unit, since, until)
    return qs.values("person_id").distinct().count()



def kpi_coverage(programme=None, unit=None, since=None, until=None):
    """% of headcount at level 2+ on at least one programme competency.

    Denominator: sum of unit_headcount for the matching units and their categories.
    Numerator: distinct people with evidence at level 2+ on any programme competency.
    """
    # Numerator: people with level 2+ evidence on a programme competency
    ev_qs = Evidence.objects.filter(
        status="valid", level__ordinal__gte=2
    ).exclude(type="self")
    if programme:
        ev_qs = ev_qs.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if unit:
        ev_qs = ev_qs.filter(person__unit=unit)
    if since:
        ev_qs = ev_qs.filter(assessed_at__gte=since)
    if until:
        ev_qs = ev_qs.filter(assessed_at__lte=until)
    numerator = ev_qs.values("person_id").distinct().count()

    # Denominator: total headcount
    hc_qs = UnitHeadcount.objects.all()
    if unit:
        hc_qs = hc_qs.filter(unit=unit)
    denominator = hc_qs.aggregate(total=Max("headcount"))["total"] or 0

    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "percent": None}

    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round((numerator / denominator) * 100, 1),
    }








def kpi_progression(programme=None, unit=None, since=None, until=None):
    """Mean change in level between baseline and latest evidence per person/competency."""
    ev_qs = Evidence.objects.filter(status="valid").exclude(type="self")
    if programme:
        ev_qs = ev_qs.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if unit:
        ev_qs = ev_qs.filter(person__unit=unit)

    # For each person/competency, compute first and last level
    grouped = ev_qs.values("person_id", "competency_id").annotate(
        first_level=Min("level__ordinal"),
        last_level=Max("level__ordinal"),
        first_at=Min("assessed_at"),
        last_at=Max("assessed_at"),
    )

    deltas = []
    for row in grouped:
        # Only measure progression where there are at least two distinct timestamps
        if row["first_at"] and row["last_at"] and row["first_at"] < row["last_at"]:
            if row["first_level"] and row["last_level"]:
                deltas.append(row["last_level"] - row["first_level"])

    if not deltas:
        return {"count": 0, "mean_delta": 0.0}

    return {
        "count": len(deltas),
        "mean_delta": round(sum(deltas) / len(deltas), 2),
    }



# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------


def kpi_csat(programme=None, unit=None, since=None, until=None):
    """Mean CSAT across exit surveys."""
    qs = SurveyResponse.objects.filter(type="exit", csat__isnull=False)
    if programme:
        qs = qs.filter(attendance__session__programme=programme)
    if unit:
        qs = qs.filter(attendance__person__unit=unit)
    if since:
        qs = qs.filter(submitted_at__gte=since)
    if until:
        qs = qs.filter(submitted_at__lte=until)

    result = qs.aggregate(mean=Avg("csat"), count=Count("id"))
    return {
        "mean": round(result["mean"], 2) if result["mean"] else None,
        "count": result["count"],
    }


# ---------------------------------------------------------------------------
# Cascade multiplier
# ---------------------------------------------------------------------------


def kpi_cascade(programme=None):
    """Mean cascade multiplier across trainers (direct + second-gen) / direct."""
    from apps.programmes.services.tier_engine import (
        _check_min_mean_csat,  # reuse for reference if needed
    )

    # Direct trainees per trainer
    direct = (
        Facilitation.objects.filter(session__attendances__status="completed")
        .values("person_id", "session__programme_id")
        .annotate(
            direct_trainees=Count("session__attendances__person_id", distinct=True)
        )
    )
    if programme:
        direct = direct.filter(session__programme=programme)

    multipliers = []
    for row in direct:
        trainer_id = row["person_id"]
        prog_id = row["session__programme_id"]
        direct_count = row["direct_trainees"]

        if direct_count == 0:
            continue

        # Second-generation: trainees trained by this trainer's direct trainees
        direct_trainee_ids = (
            Facilitation.objects.filter(
                person_id=trainer_id, session__programme_id=prog_id
            )
            .values_list("session__attendances__person_id", flat=True)
            .distinct()
        )
        second_gen = (
            Facilitation.objects.filter(
                person_id__in=[tid for tid in direct_trainee_ids if tid],
                session__programme_id=prog_id,
            )
            .values_list("session__attendances__person_id", flat=True)
            .distinct()
            .count()
        )
        multiplier = (direct_count + second_gen) / direct_count
        multipliers.append(multiplier)

    if not multipliers:
        return {"count": 0, "mean_multiplier": 0.0}

    return {
        "count": len(multipliers),
        "mean_multiplier": round(sum(multipliers) / len(multipliers), 2),
    }


# ---------------------------------------------------------------------------
# Trainer pipeline
# ---------------------------------------------------------------------------


def kpi_pipeline(programme=None):
    """Count per tier across all TierStatus rows."""
    from apps.programmes.models import TierStatus

    qs = TierStatus.objects.filter(status="approved")
    if programme:
        qs = qs.filter(programme=programme)

    counts = qs.values("tier").annotate(n=Count("id"))
    tiers = {
        1: "Explorer",
        2: "Practitioner",
        3: "Associate",
        4: "Certified",
        5: "Master",
    }
    result = {tiers[t]: 0 for t in tiers}
    for row in counts:
        if row["tier"] in tiers:
            result[tiers[row["tier"]]] = row["n"]
    return result


# ---------------------------------------------------------------------------
# Reach by unit (for chart)
# ---------------------------------------------------------------------------


def kpi_reach_by_unit(programme=None, since=None, until=None):
    """List of (unit_name, distinct people) for the bar chart."""
    qs = _attendance_qs(programme, None, since, until)
    rows = (
        qs.values("person__unit__name")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"unit": row["person__unit__name"] or "(unassigned)", "people": row["people"]}
        for row in rows
        if row["person__unit__name"]
    ]
    
    
# ---------------------------------------------------------------------------
# Breakdowns for the executive memo
# ---------------------------------------------------------------------------


def kpi_reach_by_gender(programme=None, unit=None, since=None):
    """Reach grouped by gender. Excludes blanks and unknowns."""
    qs = _attendance_qs(programme, unit, since, None)
    rows = (
        qs.exclude(person__gender="")
        .values("person__gender")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [{"gender": row["person__gender"], "people": row["people"]} for row in rows]


def kpi_reach_by_category(programme=None, unit=None, since=None):
    """Reach grouped by staff/student category (academic, non_academic, etc.)."""
    qs = _attendance_qs(programme, unit, since, None)
    rows = (
        qs.exclude(person__category="")
        .values("person__category")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [{"category": row["person__category"], "people": row["people"]} for row in rows]


def kpi_reach_by_rank(programme=None, unit=None, since=None):
    """Reach grouped by rank or student level."""
    qs = _attendance_qs(programme, unit, since, None)
    rows = (
        qs.exclude(person__rank_or_level="")
        .values("person__rank_or_level")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"rank": row["person__rank_or_level"], "people": row["people"]} for row in rows
    ]


def kpi_reach_by_programme(since=None):
    """Reach grouped by programme."""
    qs = _attendance_qs(None, None, since, None)
    rows = (
        qs.values("session__programme__name")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"programme": row["session__programme__name"], "people": row["people"]}
        for row in rows
        if row["session__programme__name"]
    ]


def kpi_reach_by_mode(programme=None, since=None):
    """Reach grouped by delivery mode (in person / hybrid / online)."""
    qs = _attendance_qs(programme, None, since, None)
    rows = (
        qs.values("session__mode")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [{"mode": row["session__mode"], "people": row["people"]} for row in rows]


def kpi_affiliation_split(programme=None, since=None):
    """Reach split by affiliation: staff, student, external."""
    qs = _attendance_qs(programme, None, since, None)
    rows = (
        qs.values("person__affiliation")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"affiliation": row["person__affiliation"], "people": row["people"]}
        for row in rows
    ]


def kpi_csat_distribution(programme=None, since=None):
    """Count of CSAT responses per score 1-5."""
    qs = SurveyResponse.objects.filter(type="exit", csat__isnull=False)
    if programme:
        qs = qs.filter(attendance__session__programme=programme)
    if since:
        qs = qs.filter(submitted_at__gte=since)

    counts = qs.values("csat").annotate(n=Count("id"))
    result = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for row in counts:
        if row["csat"] in result:
            result[row["csat"]] = row["n"]
    total = sum(result.values())
    return {"counts": result, "total": total}


def kpi_evidence_by_type(programme=None, since=None):
    """Count of evidence rows by evidence type."""
    qs = Evidence.objects.filter(status="valid")
    if programme:
        qs = qs.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if since:
        qs = qs.filter(assessed_at__gte=since)

    rows = qs.values("type").annotate(n=Count("id")).order_by("-n")
    return [{"type": row["type"], "count": row["n"]} for row in rows]


def kpi_sessions_by_month(programme=None, since=None):
    """Sessions grouped by month."""
    from django.db.models.functions import TruncMonth

    qs = Session.objects.all()
    if programme:
        qs = qs.filter(programme=programme)
    if since:
        qs = qs.filter(start_at__gte=since)

    rows = (
        qs.annotate(month=TruncMonth("start_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )
    return [
        {
            "month": row["month"].strftime("%Y-%m") if row["month"] else "",
            "count": row["count"],
        }
        for row in rows
        if row["month"]
    ]


def kpi_pipeline_funnel():
    """Full funnel: how many people at each stage."""
    from apps.programmes.models import TierStatus

    # Total people with any evidence at all (any stage)
    total_with_evidence = (
        Evidence.objects.filter(status="valid")
        .exclude(type="self")
        .values("person_id")
        .distinct()
        .count()
    )

    funnel = {
        "with_evidence": total_with_evidence,
        "eligible": 0,
        "nominated": 0,
        "approved": 0,
        "certified": 0,
        "master": 0,
    }

    for row in TierStatus.objects.all().values("status", "tier").annotate(
        n=Count("person_id", distinct=True)
    ):
        if row["status"] == "eligible":
            funnel["eligible"] += row["n"]
        elif row["status"] == "nominated":
            funnel["nominated"] += row["n"]
        elif row["status"] == "approved":
            funnel["approved"] += row["n"]
            if row["tier"] >= 4:
                funnel["certified"] += row["n"]
            if row["tier"] == 5:
                funnel["master"] += row["n"]

    return funnel


def kpi_cascade_top(limit=10):
    """Top trainers by cascade multiplier."""
    from apps.sessions.models import Facilitation

    trainers = Facilitation.objects.values("person_id").distinct()
    results = []

    for t in trainers:
        trainer_id = t["person_id"]
        direct = (
            Facilitation.objects.filter(person_id=trainer_id)
            .values_list("session__attendances__person_id", flat=True)
            .distinct()
        )
        direct_ids = [tid for tid in direct if tid and tid != trainer_id]
        direct_count = len(direct_ids)
        if direct_count == 0:
            continue

        second_gen_count = (
            Facilitation.objects.filter(person_id__in=direct_ids)
            .values_list("session__attendances__person_id", flat=True)
            .distinct()
            .exclude(person_id__in=direct_ids)
            .exclude(person_id=trainer_id)
            .count()
        )
        multiplier = (direct_count + second_gen_count) / direct_count

        trainer = Person.objects.filter(id=trainer_id).first()
        if trainer:
            results.append(
                {
                    "trainer": trainer.full_name,
                    "direct": direct_count,
                    "second_gen": second_gen_count,
                    "multiplier": round(multiplier, 2),
                }
            )

    results.sort(key=lambda r: r["multiplier"], reverse=True)
    return results[:limit]


def kpi_reach_by_region(programme=None, since=None):
    """External reach grouped by organisation region."""
    qs = _attendance_qs(programme, None, since, None)
    rows = (
        qs.exclude(person__organisation__region="")
        .exclude(person__organisation__region__isnull=True)
        .values("person__organisation__region")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"region": row["person__organisation__region"], "people": row["people"]}
        for row in rows
    ]


def kpi_coverage_by_unit(programme=None):
    """Coverage % per college/directorate - for a bar chart."""
    from apps.registry.models import OrgUnit

    results = []
    for unit in OrgUnit.objects.filter(type__in=["college", "directorate"]):
        cov = kpi_coverage(programme=programme, unit=unit)
        if cov["denominator"] > 0:
            results.append(
                {
                    "unit": unit.name,
                    "numerator": cov["numerator"],
                    "denominator": cov["denominator"],
                    "percent": cov["percent"],
                }
            )

    results.sort(key=lambda r: r["percent"] or 0, reverse=True)
    return results 


def kpi_reach_by_session_region(programme=None, since=None):
    """Reach by the session's region field - off-campus sessions."""
    qs = _attendance_qs(programme, None, since, None)
    rows = (
        qs.exclude(session__region="")
        .values("session__region")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("-people")
    )
    return [
        {"region": row["session__region"], "people": row["people"]}
        for row in rows
    ]  
    
    
    
def kpi_workshops_held(programme=None, unit=None, since=None, until=None):
    """Total number of sessions delivered."""
    from apps.sessions.models import Session
    from django.utils import timezone
    from datetime import datetime

    qs = Session.objects.filter(
        status__in=["closed", "open", "scheduled"]
    )

    if programme:
        qs = qs.filter(programme=programme)
    if unit:
        qs = qs.filter(host_unit=unit)
    if since:
        qs = qs.filter(start_at__gte=since)
    if until:
        qs = qs.filter(start_at__lte=until)

    return qs.count()


def kpi_baseline_coverage(programme=None, unit=None):
    """Percentage of Person records that have submitted a self-assessment."""
    from apps.passport.models import SelfAssessment
    from apps.registry.models import Person

    person_qs = Person.objects.filter(is_anonymised=False)
    if unit:
        person_qs = person_qs.filter(unit=unit)
    total = person_qs.count()

    if total == 0:
        return {"count": 0, "total": 0, "percent": None}

    assessed_person_ids = SelfAssessment.objects.values_list(
        "person_id", flat=True
    ).distinct()
    assessed = person_qs.filter(id__in=assessed_person_ids).count()

    return {
        "count": assessed,
        "total": total,
        "percent": round(assessed / total * 100),
    }


def kpi_ai_coverage_by_college():
    """Percentage of each college's people with level 2+ on AI-01 to AI-04."""
    from apps.library.models import Competency
    from apps.passport.models import Evidence
    from apps.registry.models import OrgUnit, Person
    from django.db.models import Count, Q

    ai_codes = ["AI-01", "AI-02", "AI-03", "AI-04"]
    ai_competencies = Competency.objects.filter(code__in=ai_codes)

    # People with level 2+ evidence on any AI competency
    qualified_person_ids = set(
        Evidence.objects.filter(
            competency__in=ai_competencies,
            level__ordinal__gte=2,
            status="valid",
        ).exclude(type="self").values_list("person_id", flat=True).distinct()
    )

    rows = []
    colleges = OrgUnit.objects.filter(type="college", active=True).order_by("name")

    for college in colleges:
        # Count people in this college (or its child units)
        college_unit_ids = [college.id] + list(
            OrgUnit.objects.filter(parent=college).values_list("id", flat=True)
        )
        total_people = Person.objects.filter(
            unit_id__in=college_unit_ids, is_anonymised=False
        ).count()

        if total_people == 0:
            continue

        qualified_people = Person.objects.filter(
            unit_id__in=college_unit_ids,
            is_anonymised=False,
            id__in=qualified_person_ids,
        ).count()

        rows.append({
            "unit": college.short_name or college.name,
            "people": qualified_people,
            "total": total_people,
            "percent": round(qualified_people / total_people * 100),
        })

    return rows


def kpi_attendances_by_month(programme=None, unit=None, since=None, until=None):
    """Completed attendances per month (for the line chart)."""
    from django.db.models.functions import TruncMonth
    from django.db.models import Count

    qs = _attendance_qs(programme, unit, since, until).filter(
        status="completed"
    )
    rows = (
        qs.annotate(month=TruncMonth("check_in_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )

    result = []
    for row in rows:
        if row["month"]:
            result.append({
                "month": row["month"].strftime("%b"),
                "count": row["count"],
            })
    return result   



def get_recommended_sessions(person, limit=3):
    """Find upcoming sessions that teach competencies the person is missing."""
    from apps.sessions.models import Session
    from apps.programmes.models import ProgrammeCompetency
    from apps.passport.models import Evidence
    from django.utils import timezone

    # 1. Get all competencies the person has evidence for
    current_competencies = set(
        Evidence.objects.filter(person=person, status="valid")
        .values_list("competency_id", flat=True)
        .distinct()
    )

    # 2. Get all target competencies for the programmes they are involved in
    # For simplicity, we look at programmes where they have a TierStatus
    from apps.programmes.models import TierStatus
    programme_ids = TierStatus.objects.filter(person=person).values_list("programme_id", flat=True)
    
    target_competencies = ProgrammeCompetency.objects.filter(
        programme_id__in=programme_ids
    ).values_list("competency_id", flat=True).distinct()

    # 3. The gaps are target competencies they don't have evidence for yet
    gaps = set(target_competencies) - current_competencies

    if not gaps:
        return [] # No gaps found

    # 4. Find upcoming sessions that teach any of the gap competencies
    now = timezone.now()
    upcoming_sessions = (
        Session.objects.filter(
            start_at__gte=now,
            status__in=["scheduled", "open"],
            competencies__competency_id__in=gaps
        )
        .distinct()
        .order_by("start_at")[:limit]
    )

    return upcoming_sessions 
    