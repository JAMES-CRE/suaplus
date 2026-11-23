"""Tier engine evaluates TierCriterion against Evidence to compute TierStatus.

Runs nightly via Celery. Idempotent: safe to run multiple times.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from django.db import transaction
from django.db.models import Avg, Q
from django.utils import timezone

from apps.passport.models import Evidence
from apps.passport.querysets import valid_evidence
from apps.programmes.models import (
    Programme,
    TierCriterion,
    TierStatus,
)
from apps.registry.models import Person
from apps.sessions.models import Attendance, Facilitation, SurveyResponse

logger = logging.getLogger(__name__)


@dataclass
class CriterionResult:
    """The outcome of checking one tier criterion against one person."""

    criterion: TierCriterion
    met: bool
    detail: str = ""


@dataclass
class TierEvaluation:
    """The full evaluation for one person × programme × tier."""

    person_id: int
    programme_id: int
    tier: int
    criteria_total: int
    criteria_met: int
    results: list[CriterionResult] = field(default_factory=list)

    @property
    def all_met(self) -> bool:
        return self.criteria_total > 0 and self.criteria_met == self.criteria_total


def _programme_evidence(person, programme):
    return (
        valid_evidence()
        .filter(
            person=person,
            competency__in=programme.competency_targets.values("competency"),
        )
        .exclude(type="self")
    )


def _completed_facilitations(person, programme):
    return Facilitation.objects.filter(
        person=person,
        session__programme=programme,
        session__status="closed",
        session__end_at__lte=timezone.now(),
        session__attendances__status="completed",
    ).distinct()


def _recent_activity(person, programme, cutoff):
    return (
        _programme_evidence(person, programme).filter(assessed_at__gte=cutoff).exists()
        or Attendance.objects.filter(
            person=person,
            session__programme=programme,
            status="completed",
            check_out_at__gte=cutoff,
        )
        .exclude(session__status="cancelled")
        .exists()
        or _completed_facilitations(person, programme)
        .filter(session__end_at__gte=cutoff)
        .exists()
    )


def _check_min_level(criterion, person, programme):
    """Is the person at the required level on the required competency?"""
    params = criterion.parameters
    code = params.get("competency")
    target_level = params.get("level", 3)
    required_evidence = params.get("evidence", [])

    if not code:
        return CriterionResult(criterion, False, "no competency specified")

    ev_qs = _programme_evidence(person, programme).filter(
        competency__code=code,
    )
    if required_evidence:
        ev_qs = ev_qs.filter(type__in=required_evidence)

    if not ev_qs.exists():
        return CriterionResult(criterion, False, f"no valid evidence for {code}")

    best = ev_qs.order_by("-level__ordinal").first()
    met = best.level.ordinal >= target_level
    return CriterionResult(
        criterion, met, f"{code} at L{best.level.ordinal} (need L{target_level})"
    )


def _check_required_credential(criterion, person, programme):
    """Does the person have a valid credential?"""
    params = criterion.parameters
    credential_title = params.get("title", "")
    if not credential_title:
        return CriterionResult(criterion, False, "no credential title")

    from apps.passport.models import Credential

    cred = (
        Credential.objects.filter(
            person=person,
            title__icontains=credential_title,
            review_status="approved",
            status="valid",
        )
        .filter(
            Q(
                catalogue__active=True,
                catalogue__competencies__competency__in=programme.competency_targets.values(
                    "competency"
                ),
            )
            | Q(
                internalassessment__competency__in=programme.competency_targets.values(
                    "competency"
                ),
                internalassessment__attendance__status="completed",
            )
        )
        .exclude(expires_at__lt=timezone.localdate())
        .first()
    )

    met = cred is not None
    return CriterionResult(
        criterion,
        met,
        f"credential '{credential_title}' {'found' if met else 'missing'}",
    )


def _check_min_sessions(criterion, person, programme):
    """Has the person co-facilitated enough sessions?"""
    params = criterion.parameters
    required = params.get("sessions", 2)
    count = (
        _completed_facilitations(person, programme)
        .values("session_id")
        .distinct()
        .count()
    )
    met = count >= required
    return CriterionResult(criterion, met, f"{count} sessions (need {required})")


def _check_min_mean_csat(criterion, person, programme):
    """Is the mean CSAT of their sessions above the threshold?"""
    params = criterion.parameters
    threshold = params.get("threshold", 4.0)
    sessions = _completed_facilitations(person, programme).values_list(
        "session_id", flat=True
    )
    response_qs = SurveyResponse.objects.filter(
        attendance__session_id__in=sessions,
        attendance__status="completed",
        type="exit",
        csat__isnull=False,
    )
    avg = response_qs.aggregate(Avg("csat"))["csat__avg"] or 0
    met = avg >= threshold
    return CriterionResult(criterion, met, f"mean CSAT {avg:.1f} (need {threshold})")


def _check_recency_window(criterion, person, programme):
    """Has the person had any activity within the last N months?"""
    params = criterion.parameters
    months = params.get("months", 12)
    cutoff = timezone.now() - relativedelta(months=months)
    met = _recent_activity(person, programme, cutoff)
    return CriterionResult(criterion, met, f"active in last {months} months: {met}")


def evaluate_tier(person, programme, tier):
    """Evaluate all criteria for one person × programme × tier."""
    criteria = TierCriterion.objects.filter(programme=programme, tier=tier)
    results = []
    handlers = {
        "min_level": _check_min_level,
        "required_credential": _check_required_credential,
        "min_sessions": _check_min_sessions,
        "min_mean_csat": _check_min_mean_csat,
        "recency_window": _check_recency_window,
    }
    for criterion in criteria:
        handler = handlers.get(criterion.rule_type)
        if handler is None:
            results.append(CriterionResult(criterion, False, "unknown rule type"))
            continue
        results.append(handler(criterion, person, programme))

    return TierEvaluation(
        person_id=person.id,
        programme_id=programme.id,
        tier=tier,
        criteria_total=len(results),
        criteria_met=sum(1 for r in results if r.met),
        results=results,
    )


def compute_tier_status(programme=None):
    """Run the tier engine for all programmes (or one) and all people with evidence.

    Returns a summary dict.
    """
    programmes = Programme.objects.filter(status="active")
    if programme:
        programmes = programmes.filter(id=programme.id)

    summary = {"programmes_processed": 0, "statuses_written": 0, "errors": 0}

    with transaction.atomic():
        for prog in programmes:
            summary["programmes_processed"] += 1
            person_ids = set(
                Evidence.objects.filter(
                    competency__in=prog.competency_targets.values("competency")
                ).values_list("person_id", flat=True)
            )
            person_ids |= set(
                Attendance.objects.filter(session__programme=prog).values_list(
                    "person_id", flat=True
                )
            )
            person_ids |= set(
                Facilitation.objects.filter(session__programme=prog).values_list(
                    "person_id", flat=True
                )
            )

            person_ids |= set(
                TierStatus.objects.filter(programme=prog).values_list(
                    "person_id", flat=True
                )
            )

            tiers_defined = (
                TierCriterion.objects.filter(programme=prog)
                .values_list("tier", flat=True)
                .distinct()
            )

            for person_id in person_ids:
                person = Person.objects.filter(
                    id=person_id, is_anonymised=False, merged_into__isnull=True
                ).first()
                if not person:
                    continue
                for tier in tiers_defined:
                    try:
                        with transaction.atomic():
                            evaluation = evaluate_tier(person, prog, tier)
                            _write_tier_status(evaluation, prog, tier)
                        summary["statuses_written"] += 1
                    except Exception:
                        logger.exception(
                            "Tier evaluation failed for person=%s programme=%s tier=%s",
                            person_id,
                            prog.pk,
                            tier,
                        )
                        summary["errors"] += 1

    return summary


def _write_tier_status(evaluation, programme, tier):
    """Upsert a TierStatus row based on the evaluation.

    Dormancy rule (TOT-06): an already-approved trainer with no evidence,
    attendance, or facilitation in the last 365 days is flagged dormant.
    """
    status = "eligible" if evaluation.all_met else "ineligible"

    existing = TierStatus.objects.filter(
        person_id=evaluation.person_id, programme=programme, tier=tier
    ).first()

    # Evaluation cannot silently approve a nomination. Approved trainers can
    # become dormant, and resume their approval when valid programme activity returns.
    if existing and existing.status in ("approved", "dormant"):
        cutoff = timezone.now() - timedelta(days=365)
        next_status = (
            "approved"
            if _recent_activity(existing.person, programme, cutoff)
            else "dormant"
        )
        if existing.status != next_status:
            existing.status = next_status
            existing.save(update_fields=["status", "updated_at"])
        return existing
    if existing and existing.status == "nominated":
        status = "nominated"

    obj, _ = TierStatus.objects.update_or_create(
        person_id=evaluation.person_id,
        programme=programme,
        tier=tier,
        defaults={
            "status": status,
            "criteria_met": evaluation.criteria_met,
            "criteria_total": evaluation.criteria_total,
            "progressed_criteria": [
                {
                    "criterion": r.criterion.rule_type,
                    "met": r.met,
                    "detail": r.detail,
                }
                for r in evaluation.results
            ],
        },
    )
    return obj
