"""KPI computations - pure functions that aggregate from the database.

Each function takes optional filters and returns a scalar or a list.
The dashboard view calls these to build the tiles.

All functions exclude self-reported evidence from progression/coverage -
that matches the spec's rule that self-ratings never count toward KPIs.
"""

from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.accounts.permissions import unit_descendants
from apps.passport.models import Evidence
from apps.passport.querysets import valid_evidence
from apps.registry.models import Person, UnitHeadcount
from apps.reporting.access import records as _records
from apps.sessions.models import Attendance, Facilitation, Session, SurveyResponse


def suppress(count, threshold=5):
    if count is None:
        return "-"
    return "<5" if count < threshold else count


def _attendance_qs(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Base queryset of completed/partial attendances, with optional filters."""
    qs = (
        _records(Attendance)
        .filter(
            status__in=["completed", "partial"],
            person__is_anonymised=False,
            person__merged_into__isnull=True,
        )
        .exclude(session__status="cancelled")
    )
    if programme:
        qs = qs.filter(session__programme=programme)
    if unit:
        qs = qs.filter(person__unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(person__category=category)
    if affiliation:
        qs = qs.filter(person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        ).distinct()
    if since:
        qs = qs.filter(check_in_at__gte=since)
    if until:
        qs = qs.filter(check_in_at__lte=until)
    return qs


def _headcount_total(
    queryset, unit=None, since=None, until=None, category=None, affiliation=None
):
    """Latest matching snapshot; parent category roll-ups replace descendants."""
    if affiliation == "external":
        return 0
    qs = queryset.filter(period__start_date__lte=(until or timezone.now()).date())
    if since:
        qs = qs.filter(period__end_date__gte=since.date())
    if unit:
        qs = qs.filter(unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(category=category)
    if affiliation == "staff":
        qs = qs.filter(category__in=["academic", "non_academic"])
    elif affiliation == "student":
        qs = qs.filter(category__in=["undergrad", "postgrad"])
    period_id = (
        qs.order_by("-period__start_date", "-period_id")
        .values_list("period_id", flat=True)
        .first()
    )
    if period_id is None:
        return 0
    rows = list(
        qs.filter(period_id=period_id).values("unit_id", "category", "headcount")
    )
    from apps.registry.models import OrgUnit

    parents = dict(OrgUnit.objects.values_list("pk", "parent_id"))
    keys = {(row["unit_id"], row["category"]) for row in rows}
    total = 0
    for row in rows:
        parent, seen, covered = parents.get(row["unit_id"]), set(), False
        while parent is not None and parent not in seen:
            seen.add(parent)
            if (parent, row["category"]) in keys:
                covered = True
                break
            parent = parents.get(parent)
        if not covered:
            total += row["headcount"]
    return total


def _progression_rows(qs):
    """Chronological deltas, retaining zero and negative changes."""
    bounds = {}
    rows = qs.order_by("person_id", "competency_id", "assessed_at", "pk").values(
        "person_id",
        "competency_id",
        "competency__domain_id",
        "competency__domain__name",
        "assessed_at",
        "level__ordinal",
        "pk",
    )
    for row in rows:
        key = (row["person_id"], row["competency_id"])
        if key not in bounds:
            bounds[key] = [row, row]
        else:
            bounds[key][1] = row
    return [
        (first, last["level__ordinal"] - first["level__ordinal"])
        for first, last in bounds.values()
        if first["assessed_at"] < last["assessed_at"]
    ]


def kpi_reach(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Distinct people trained in the period."""
    qs = _attendance_qs(
        programme, unit, since, until, category, affiliation, partner_id
    )
    return qs.values("person_id").distinct().count()


def kpi_coverage(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """% of headcount at level 2+ on at least one programme competency.

    Denominator: sum of unit_headcount for the matching units and their categories.
    Numerator: distinct people with evidence at level 2+ on any programme competency.
    """
    if affiliation == "external":
        return {"numerator": 0, "denominator": 0, "percent": None}
    # Numerator: people with level 2+ evidence on a programme competency
    ev_qs = (
        valid_evidence(_records(Evidence))
        .filter(
            status="valid",
            level__ordinal__gte=2,
            person__affiliation__in=["staff", "student"],
        )
        .exclude(type="self")
    )
    if programme:
        ev_qs = ev_qs.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if unit:
        ev_qs = ev_qs.filter(person__unit_id__in=unit_descendants(unit.pk))
    if category:
        ev_qs = ev_qs.filter(person__category=category)
    if affiliation:
        ev_qs = ev_qs.filter(person__affiliation=affiliation)
    if partner_id:
        ev_qs = ev_qs.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        )
    if since:
        ev_qs = ev_qs.filter(assessed_at__gte=since)
    if until:
        ev_qs = ev_qs.filter(assessed_at__lte=until)
    numerator = ev_qs.values("person_id").distinct().count()

    denominator = _headcount_total(
        _records(UnitHeadcount), unit, since, until, category, affiliation
    )

    if denominator == 0:
        return {"numerator": numerator, "denominator": 0, "percent": None}

    return {
        "numerator": numerator,
        "denominator": denominator,
        "percent": round((numerator / denominator) * 100, 1),
    }


def kpi_progression(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Mean change in level between baseline and latest evidence per person/competency."""
    ev_qs = (
        valid_evidence(_records(Evidence)).filter(status="valid").exclude(type="self")
    )
    if programme:
        ev_qs = ev_qs.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if unit:
        ev_qs = ev_qs.filter(person__unit_id__in=unit_descendants(unit.pk))
    if category:
        ev_qs = ev_qs.filter(person__category=category)
    if affiliation:
        ev_qs = ev_qs.filter(person__affiliation=affiliation)
    if partner_id:
        ev_qs = ev_qs.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        )
    if since:
        ev_qs = ev_qs.filter(assessed_at__gte=since)
    if until:
        ev_qs = ev_qs.filter(assessed_at__lte=until)

    deltas = [delta for _, delta in _progression_rows(ev_qs)]

    if not deltas:
        return {"count": 0, "mean_delta": 0.0}

    return {
        "count": len(deltas),
        "mean_delta": round(sum(deltas) / len(deltas), 2),
    }


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------


def kpi_csat(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Mean CSAT across exit surveys."""
    qs = (
        _records(SurveyResponse)
        .filter(
            type="exit",
            csat__isnull=False,
            attendance__status="completed",
            attendance__person__is_anonymised=False,
            attendance__person__merged_into__isnull=True,
        )
        .exclude(attendance__session__status="cancelled")
    )
    if programme:
        qs = qs.filter(attendance__session__programme=programme)
    if unit:
        qs = qs.filter(attendance__person__unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(attendance__person__category=category)
    if affiliation:
        qs = qs.filter(attendance__person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(
            Q(attendance__session__partners__partner_id=partner_id)
            | Q(attendance__session__programme__partners__partner_id=partner_id)
        ).distinct()
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


def _cascade_rows(
    programme=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Count source-scoped completed trainees, with chronological cascade edges."""
    facilitations = (
        _records(Facilitation)
        .filter(
            person__is_anonymised=False,
            person__merged_into__isnull=True,
        )
        .exclude(session__status="cancelled")
    )
    if programme:
        facilitations = facilitations.filter(session__programme=programme)
    if since:
        facilitations = facilitations.filter(session__start_at__gte=since)
    if until:
        facilitations = facilitations.filter(session__start_at__lte=until)
    if partner_id:
        facilitations = facilitations.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        )
    attendances = _attendance_qs(
        programme, None, since, until, None, None, partner_id
    ).filter(status="completed")
    by_session = {}
    for row in attendances.values("session_id", "person_id", "check_out_at"):
        by_session.setdefault(row["session_id"], []).append(row)
    edges, names = {}, {}
    for row in facilitations.values(
        "person_id",
        "person__full_name",
        "person__category",
        "person__affiliation",
        "session_id",
        "session__programme_id",
        "session__start_at",
    ).distinct():
        key = (row["person_id"], row["session__programme_id"])
        names[key] = row
        for attendance in by_session.get(row["session_id"], []):
            if attendance["person_id"] != row["person_id"]:
                edges.setdefault(key, []).append(
                    (
                        attendance["person_id"],
                        attendance["check_out_at"],
                        row["session__start_at"],
                    )
                )
    result = []
    for (trainer, prog), training in edges.items():
        info = names[(trainer, prog)]
        if (category and info["person__category"] != category) or (
            affiliation and info["person__affiliation"] != affiliation
        ):
            continue
        direct = {}
        for trainee, completed, _ in training:
            if completed is not None:
                direct[trainee] = min(direct.get(trainee, completed), completed)
        second = set()
        for trainee, completed in direct.items():
            for downstream, _, started in edges.get((trainee, prog), []):
                if (
                    started >= completed
                    and downstream != trainer
                    and downstream not in direct
                ):
                    second.add(downstream)
        if direct:
            result.append(
                {
                    "trainer": info["person__full_name"],
                    "direct": len(direct),
                    "second_generation": len(second),
                    "multiplier": (len(direct) + len(second)) / len(direct),
                }
            )
    return sorted(result, key=lambda row: (-row["multiplier"], row["trainer"]))


def kpi_cascade(
    programme=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    rows = _cascade_rows(programme, since, until, category, affiliation, partner_id)
    return {
        "count": len(rows),
        "mean_multiplier": round(sum(r["multiplier"] for r in rows) / len(rows), 2)
        if rows
        else 0.0,
    }


def kpi_pipeline(
    programme=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Count per tier across all TierStatus rows."""
    from apps.programmes.models import TierStatus

    qs = _records(TierStatus).filter(
        status="approved", person__is_anonymised=False, person__merged_into__isnull=True
    )
    if programme:
        qs = qs.filter(programme=programme)
    if category:
        qs = qs.filter(person__category=category)
    if affiliation:
        qs = qs.filter(person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(programme__partners__partner_id=partner_id)
    if since:
        qs = qs.filter(approved_at__gte=since)
    if until:
        qs = qs.filter(approved_at__lte=until)

    highest = {}
    for person_id, tier in qs.values_list("person_id", "tier"):
        highest[person_id] = max(highest.get(person_id, 0), tier)
    counts = [
        {"tier": tier, "n": list(highest.values()).count(tier)}
        for tier in set(highest.values())
    ]
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


def kpi_reach_by_unit(
    programme=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """List of (unit_name, distinct people) for the bar chart."""
    qs = _attendance_qs(
        programme, None, since, until, category, affiliation, partner_id
    )
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
    return [
        {"category": row["person__category"], "people": row["people"]} for row in rows
    ]


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


def kpi_reach_by_programme(since=None, programme=None):
    """Reach grouped by programme."""
    qs = _attendance_qs(programme, None, since, None)
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
    qs = (
        _records(SurveyResponse)
        .filter(
            type="exit",
            csat__isnull=False,
            attendance__status="completed",
            attendance__person__is_anonymised=False,
            attendance__person__merged_into__isnull=True,
        )
        .exclude(attendance__session__status="cancelled")
    )
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
    qs = valid_evidence(_records(Evidence)).filter(status="valid")
    if programme:
        qs = qs.filter(competency__in=programme.competency_targets.values("competency"))
    if since:
        qs = qs.filter(assessed_at__gte=since)

    rows = qs.values("type").annotate(n=Count("id")).order_by("-n")
    return [{"type": row["type"], "count": row["n"]} for row in rows]


def kpi_sessions_by_month(programme=None, since=None):
    """Sessions grouped by month."""
    from django.db.models.functions import TruncMonth

    qs = _records(Session).filter(status="closed", end_at__lte=timezone.now())
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


def kpi_pipeline_funnel(programme=None, since=None):
    """Full funnel: how many people at each stage."""
    from apps.programmes.models import TierStatus

    evidence = valid_evidence(_records(Evidence)).exclude(type="self")
    if programme:
        evidence = evidence.filter(
            competency__in=programme.competency_targets.values("competency")
        )
    if since:
        evidence = evidence.filter(assessed_at__gte=since)
    total_with_evidence = (
        evidence.filter(status="valid")
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

    statuses = _records(TierStatus).filter(
        person__is_anonymised=False, person__merged_into__isnull=True
    )
    if programme:
        statuses = statuses.filter(programme=programme)
    if since:
        statuses = statuses.filter(updated_at__gte=since)
    for status in ("eligible", "nominated", "approved"):
        funnel[status] = (
            statuses.filter(status=status).values("person_id").distinct().count()
        )
    funnel["certified"] = (
        statuses.filter(status="approved", tier__gte=4)
        .values("person_id")
        .distinct()
        .count()
    )
    funnel["master"] = (
        statuses.filter(status="approved", tier=5)
        .values("person_id")
        .distinct()
        .count()
    )

    return funnel


def kpi_cascade_top(limit=10):
    return [
        {
            "trainer": r["trainer"],
            "direct": r["direct"],
            "second_gen": r["second_generation"],
            "multiplier": round(r["multiplier"], 2),
        }
        for r in _cascade_rows()[:limit]
    ]


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


def kpi_coverage_by_unit(programme=None, since=None):
    """Coverage % per college/directorate - for a bar chart."""
    from apps.registry.models import OrgUnit

    results = []
    for unit in _records(OrgUnit).filter(type__in=["college", "directorate"]):
        cov = kpi_coverage(programme=programme, unit=unit, since=since)
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
    return [{"region": row["session__region"], "people": row["people"]} for row in rows]


def kpi_workshops_held(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Total number of sessions delivered."""

    from apps.sessions.models import Session

    qs = _records(Session).filter(status="closed", end_at__lte=timezone.now())

    if programme:
        qs = qs.filter(programme=programme)
    if unit:
        qs = qs.filter(host_unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(attendances__person__category=category)
    if affiliation:
        qs = qs.filter(attendances__person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(
            Q(partners__partner_id=partner_id)
            | Q(programme__partners__partner_id=partner_id)
        )
    if since:
        qs = qs.filter(start_at__gte=since)
    if until:
        qs = qs.filter(start_at__lte=until)

    return qs.distinct().count()


def kpi_baseline_coverage(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Percentage of Person records that have submitted a self-assessment."""
    from apps.passport.models import SelfAssessment
    from apps.registry.models import Person

    person_qs = _records(Person).filter(is_anonymised=False, merged_into__isnull=True)
    if programme:
        person_qs = person_qs.filter(
            attendances__session__programme=programme,
            attendances__status__in=["partial", "completed"],
        ).distinct()
    if unit:
        person_qs = person_qs.filter(unit_id__in=unit_descendants(unit.pk))
    if category:
        person_qs = person_qs.filter(category=category)
    if affiliation:
        person_qs = person_qs.filter(affiliation=affiliation)
    if partner_id:
        person_qs = person_qs.filter(
            Q(attendances__session__partners__partner_id=partner_id)
            | Q(attendances__session__programme__partners__partner_id=partner_id)
        ).distinct()
    total = person_qs.count()

    if total == 0:
        return {"count": 0, "total": 0, "percent": None}

    assessments = _records(SelfAssessment).filter(status="submitted")
    if since:
        assessments = assessments.filter(submitted_at__gte=since)
    if until:
        assessments = assessments.filter(submitted_at__lte=until)
    assessed_person_ids = assessments.values_list("person_id", flat=True).distinct()
    assessed = person_qs.filter(id__in=assessed_person_ids).count()

    return {
        "count": assessed,
        "total": total,
        "percent": round(assessed / total * 100),
    }


def kpi_ai_coverage_by_college(since=None, until=None):
    """Percentage of each college's people with level 2+ on AI-01 to AI-04."""

    from apps.library.models import Competency
    from apps.passport.models import Evidence
    from apps.registry.models import OrgUnit, Person

    ai_codes = ["AI-01", "AI-02", "AI-03", "AI-04"]
    ai_competencies = Competency.objects.filter(code__in=ai_codes)

    # People with level 2+ evidence on any AI competency
    evidence_qs = (
        valid_evidence(_records(Evidence))
        .filter(
            competency__in=ai_competencies,
            level__ordinal__gte=2,
            status="valid",
        )
        .exclude(type="self")
    )
    if since:
        evidence_qs = evidence_qs.filter(assessed_at__gte=since)
    if until:
        evidence_qs = evidence_qs.filter(assessed_at__lte=until)
    qualified_person_ids = set(
        evidence_qs.values_list("person_id", flat=True).distinct()
    )

    rows = []
    colleges = _records(OrgUnit).filter(type="college", active=True).order_by("name")

    for college in colleges:
        # Count people in this college (or its child units)
        college_unit_ids = unit_descendants(college.pk)
        total_people = (
            _records(Person)
            .filter(
                unit_id__in=college_unit_ids,
                is_anonymised=False,
                merged_into__isnull=True,
            )
            .count()
        )

        if total_people == 0:
            continue

        qualified_people = (
            _records(Person)
            .filter(
                unit_id__in=college_unit_ids,
                is_anonymised=False,
                merged_into__isnull=True,
                id__in=qualified_person_ids,
            )
            .count()
        )

        rows.append(
            {
                "unit": college.short_name or college.name,
                "people": qualified_people,
                "total": total_people,
                "percent": round(qualified_people / total_people * 100),
            }
        )

    return rows


def kpi_attendances_by_month(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Completed attendances per month (for the line chart)."""
    from django.db.models import Count
    from django.db.models.functions import TruncMonth

    qs = _attendance_qs(
        programme, unit, since, until, category, affiliation, partner_id
    ).filter(status="completed")
    rows = (
        qs.annotate(month=TruncMonth("check_in_at"))
        .values("month")
        .annotate(count=Count("id"))
        .order_by("month")
    )

    result = []
    for row in rows:
        if row["month"]:
            result.append(
                {
                    "month": row["month"].strftime("%b"),
                    "count": row["count"],
                }
            )
    return result


def kpi_csat_trend(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Monthly mean exit-survey CSAT for the dashboard trend chart."""
    from django.db.models.functions import TruncMonth

    qs = (
        _records(SurveyResponse)
        .filter(
            type="exit",
            csat__isnull=False,
            attendance__status="completed",
            attendance__person__is_anonymised=False,
            attendance__person__merged_into__isnull=True,
        )
        .exclude(attendance__session__status="cancelled")
    )
    if programme:
        qs = qs.filter(attendance__session__programme=programme)
    if unit:
        qs = qs.filter(attendance__person__unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(attendance__person__category=category)
    if affiliation:
        qs = qs.filter(attendance__person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(
            Q(attendance__session__partners__partner_id=partner_id)
            | Q(attendance__session__programme__partners__partner_id=partner_id)
        ).distinct()
    if since:
        qs = qs.filter(submitted_at__gte=since)
    if until:
        qs = qs.filter(submitted_at__lte=until)

    rows = (
        qs.annotate(month=TruncMonth("submitted_at"))
        .values("month")
        .annotate(mean=Avg("csat"), responses=Count("id"))
        .order_by("month")
    )
    return [
        {
            "month": row["month"].strftime("%b %Y"),
            "mean": round(row["mean"], 2),
            "responses": row["responses"],
        }
        for row in rows
        if row["month"]
    ]


def kpi_progression_by_domain(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Mean verified competency-level change, grouped by domain."""
    qs = valid_evidence(_records(Evidence)).filter(status="valid").exclude(type="self")
    if programme:
        qs = qs.filter(competency__in=programme.competency_targets.values("competency"))
    if unit:
        qs = qs.filter(person__unit_id__in=unit_descendants(unit.pk))
    if category:
        qs = qs.filter(person__category=category)
    if affiliation:
        qs = qs.filter(person__affiliation=affiliation)
    if partner_id:
        qs = qs.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        ).distinct()
    if since:
        qs = qs.filter(assessed_at__gte=since)
    if until:
        qs = qs.filter(assessed_at__lte=until)

    changes = {}
    for row, delta in _progression_rows(qs):
        key = (
            row["competency__domain_id"],
            row["competency__domain__name"] or "Unassigned",
        )
        item = changes.setdefault(key, {"deltas": [], "people": set()})
        item["deltas"].append(delta)
        item["people"].add(row["person_id"])
    return [
        {
            "domain": name,
            "mean_delta": round(sum(item["deltas"]) / len(item["deltas"]), 2),
            "people": len(item["people"]),
        }
        for (_, name), item in sorted(changes.items())
    ]


def kpi_coverage_heatmap(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """Verified level-2 coverage for each unit and competency domain."""
    from apps.library.models import Domain
    from apps.registry.models import OrgUnit

    if affiliation == "external":
        # The SRS coverage measure uses internal unit headcounts as its denominator.
        return []
    units = _records(OrgUnit).filter(type__in=["college", "directorate"])
    if unit:
        units = units.filter(pk=unit.pk)
    domains = Domain.objects.filter(status__in=["active", "published"])
    evidence = (
        valid_evidence(_records(Evidence))
        .filter(status="valid", level__ordinal__gte=2)
        .exclude(type="self")
    )
    if programme:
        evidence = evidence.filter(
            competency__in=programme.competency_targets.values("competency")
        )
        domains = domains.filter(competencies__programme_targets__programme=programme)
    if category:
        evidence = evidence.filter(person__category=category)
    if affiliation:
        evidence = evidence.filter(person__affiliation=affiliation)
    if partner_id:
        evidence = evidence.filter(
            Q(session__partners__partner_id=partner_id)
            | Q(session__programme__partners__partner_id=partner_id)
        ).distinct()
    if since:
        evidence = evidence.filter(assessed_at__gte=since)
    if until:
        evidence = evidence.filter(assessed_at__lte=until)

    headcounts = _records(UnitHeadcount).all()
    if category:
        headcounts = headcounts.filter(category=category)
    if since:
        headcounts = headcounts.filter(period__end_date__gte=since.date())
    if until:
        headcounts = headcounts.filter(period__start_date__lte=until.date())

    result = []
    for org_unit in units.distinct():
        denominator = _headcount_total(
            headcounts, org_unit, since, until, category, affiliation
        )
        if not denominator:
            continue
        for domain in domains.distinct():
            people = (
                evidence.filter(
                    person__unit_id__in=unit_descendants(org_unit.pk),
                    competency__domain=domain,
                    person__affiliation__in=["staff", "student"],
                )
                .values("person_id")
                .distinct()
                .count()
            )
            result.append(
                {
                    "unit": org_unit.name,
                    "domain": domain.name,
                    "people": people,
                    "total": denominator,
                    "percent": round(people / denominator * 100),
                }
            )
    return result


def kpi_external_reach(
    programme=None,
    unit=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
):
    """External participant reach grouped by organisation region and type."""
    if affiliation and affiliation != "external":
        return []
    qs = _attendance_qs(programme, unit, since, until, category, "external", partner_id)
    rows = (
        qs.exclude(person__organisation__isnull=True)
        .values("person__organisation__region", "person__organisation__type")
        .annotate(people=Count("person_id", distinct=True))
        .order_by("person__organisation__region", "person__organisation__type")
    )
    return [
        {
            "region": row["person__organisation__region"] or "Unspecified",
            "organisation_type": row["person__organisation__type"] or "Other",
            "people": row["people"],
        }
        for row in rows
    ]


def kpi_cascade_by_trainer(
    programme=None,
    since=None,
    until=None,
    category=None,
    affiliation=None,
    partner_id=None,
    limit=10,
):
    return [
        {**row, "multiplier": round(row["multiplier"], 2)}
        for row in _cascade_rows(
            programme, since, until, category, affiliation, partner_id
        )[:limit]
    ]


def get_recommended_sessions(person, limit=3):
    """Find upcoming sessions that teach competencies the person is missing."""
    from apps.passport.models import Evidence
    from apps.programmes.models import ProgrammeCompetency
    from apps.sessions.models import Session

    # 1. Get all competencies the person has evidence for
    current_competencies = set(
        valid_evidence(_records(Evidence))
        .filter(person=person, status="valid")
        .values_list("competency_id", flat=True)
        .distinct()
    )

    # 2. Get all target competencies for the programmes they are involved in
    # For simplicity, we look at programmes where they have a TierStatus
    from apps.programmes.models import TierStatus

    programme_ids = (
        _records(TierStatus)
        .filter(person=person)
        .values_list("programme_id", flat=True)
    )

    target_competencies = (
        ProgrammeCompetency.objects.filter(programme_id__in=programme_ids)
        .values_list("competency_id", flat=True)
        .distinct()
    )

    # 3. The gaps are target competencies they don't have evidence for yet
    gaps = set(target_competencies) - current_competencies

    if not gaps:
        return []  # No gaps found

    # 4. Find upcoming sessions that teach any of the gap competencies
    now = timezone.now()
    upcoming_sessions = (
        _records(Session)
        .filter(
            start_at__gte=now,
            status__in=["scheduled", "open"],
            competencies__competency_id__in=gaps,
        )
        .distinct()
        .order_by("start_at")[:limit]
    )

    return upcoming_sessions
