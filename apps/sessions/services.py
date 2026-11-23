"""Atomic completion and source-linked workshop evidence (SES-06, Annex A.3)."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.library.models import EvidenceRule, Level
from apps.library.versioning import framework_version_label
from apps.passport.models import Evidence
from apps.sessions.models import Attendance, Session, SessionCompetency, SurveyResponse


def set_session_competencies(session, competencies):
    """Freeze mappings once participation starts; never rewrite past evidence."""
    requested = {competency.pk for competency in competencies}
    with transaction.atomic():
        locked = Session.objects.select_for_update().get(pk=session.pk)
        previous = set(locked.competencies.values_list("competency_id", flat=True))
        if requested == previous:
            return
        if locked.attendances.exists():
            raise ValidationError("Competency mappings are locked after the first check-in.")
        allowed = set(locked.programme.competency_targets.filter(
            competency__framework__is_knust=True, competency__status__in=["active", "published"],
            competency__framework__status__in=["active", "published"], competency__domain__status__in=["active", "published"],
        ).values_list("competency_id", flat=True))
        if not requested or not requested <= allowed:
            raise ValidationError("Choose at least one active KNUST competency mapped to this programme.")
        locked.competencies.exclude(competency_id__in=requested).delete()
        SessionCompetency.objects.bulk_create([
            SessionCompetency(session=locked, competency_id=pk) for pk in requested - previous
        ])



def grant_attendance_evidence(attendance):
    """A completed workshop supports only level 1, never task/assessor levels.

    Callers lock the session and attendance before invoking this helper. A
    database uniqueness constraint also prevents duplicate source claims.
    """
    if attendance.status != "completed" or not attendance.check_in_at or not attendance.check_out_at:
        return 0
    written = 0
    for coverage in attendance.session.competencies.select_related("competency__framework"):
        competency = coverage.competency
        framework = competency.framework
        if not framework.is_knust:
            continue
        caps = EvidenceRule.objects.filter(
            Q(competency=competency) | Q(competency__isnull=True),
            evidence_type="attendance", status__in=["active", "published"],
        ).values_list("max_level", flat=True)
        if any(cap < 1 for cap in caps):
            continue
        level = Level.objects.filter(framework=framework, ordinal=1).first()
        if level is None:
            continue
        evidence, created = Evidence.objects.get_or_create(
            competency=competency, type="attendance", source_type="attendance", source_id=attendance.pk,
            defaults={
                "person": attendance.person, "session": attendance.session, "level": level,
                "framework_version": framework_version_label(framework), "status": "valid",
            },
        )
        # A fresh completion can replace the invalid pre-completion claim from
        # legacy check-in code. Replayed checkout never calls this helper.
        if not created:
            evidence.person = attendance.person
            evidence.session = attendance.session
            evidence.level = level
            evidence.credential = None
            evidence.status = "valid"
            evidence.is_expired = False
            evidence.expires_at = None
            evidence.framework_version = framework_version_label(framework)
        evidence.assessed_at = attendance.check_out_at
        evidence.full_clean()
        evidence.save()
        written += 1
    return written


def send_passport_update(person, session_title):
    """One shared, privacy-aware delivery path for imports and completion."""
    from apps.notifications.services import (
        send_passport_link_to_guardian,
        send_passport_link_to_participant,
        send_passport_link_to_partner_coordinators,
    )
    from apps.passport.linking import issue_passport_link

    passport_url = issue_passport_link(person)
    if person.age_band == "under_18":
        if send_passport_link_to_guardian(person=person, session_title=session_title, passport_url=passport_url):
            return "guardian"
        sent = send_passport_link_to_partner_coordinators(person=person, session_title=session_title, passport_url=passport_url)
        return "coordinator" if sent else "unavailable"
    deliveries = send_passport_link_to_participant(person=person, session_title=session_title, passport_url=passport_url)
    return "participant" if any(delivery.status == "sent" for delivery in (deliveries or [])) else "unavailable"


def complete_attendance(attendance_id, *, checkout_data=None, at=None):
    """Complete once; return (attendance, changed, evidence_count).

    Lock order is session → attendance throughout the app. The exit survey,
    completion timestamp and evidence commit together; replay changes nothing.
    """
    with transaction.atomic():
        session_id = Attendance.objects.values_list("session_id", flat=True).get(pk=attendance_id)
        session = Session.objects.select_for_update().get(pk=session_id)
        attendance = Attendance.objects.select_for_update().select_related("person").get(pk=attendance_id)
        attendance.session = session
        if attendance.status == "completed":
            return attendance, False, 0
        if session.status == "cancelled":
            raise ValidationError("A cancelled workshop cannot be completed.")
        if attendance.status != "partial" or attendance.check_in_at is None:
            raise ValidationError("Only a checked-in participant can be marked completed.")
        at = at or timezone.now()
        if at < attendance.check_in_at:
            raise ValidationError("Completion time cannot be before check-in.")
        attendance.check_out_at = at
        attendance.status = "completed"
        attendance.save(update_fields=["check_out_at", "status"])
        if checkout_data is not None:
            from apps.library.models import SurveyTemplate
            template = SurveyTemplate.objects.filter(type="exit", is_default=True, status__in=["active", "published"]).first()
            if template:
                SurveyResponse.objects.get_or_create(
                    attendance=attendance, type="exit",
                    defaults={
                        "template": template, "csat": checkout_data["csat"],
                        "answers": {"what_will_you_apply": checkout_data["what_will_you_apply"]},
                        "interested_in_training": checkout_data["interested_in_training"],
                    },
                )
        return attendance, True, grant_attendance_evidence(attendance)
