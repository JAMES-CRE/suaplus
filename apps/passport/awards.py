"""Source-linked internal assessment and framework-specific external evidence."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.permissions import can, person_id_for, scope_records
from apps.audit.services import log_event
from apps.library.models import CertificationCatalogue, EvidenceRule, Level
from apps.library.versioning import framework_version_label
from apps.passport.models import (
    Credential,
    CredentialReview,
    Evidence,
    InternalAssessment,
)
from apps.passport.services import generate_badge_json
from apps.sessions.models import Attendance, Session


def evidence_cap(competency, evidence_type):
    values = list(
        EvidenceRule.objects.filter(
            Q(competency=competency) | Q(competency__isnull=True),
            evidence_type=evidence_type,
            status__in=["active", "published"],
        ).values_list("max_level", flat=True)
    )
    if not values:
        raise ValidationError(
            "No active evidence rule permits this type. Ask a curator to configure it."
        )
    return min(values)


def issue_internal_award(
    attendance_id, user, *, competency, level, evidence_type, note, request=None
):
    if not can(user, "record_credentials") or not can(user, "edit_records"):
        raise PermissionDenied
    if (
        evidence_type not in {"task", "assessor"}
        or not note.strip()
        or len(note) > 2000
    ):
        raise ValidationError(
            "Choose an assessment type and give a rationale of 1–2,000 characters."
        )
    session_id = Attendance.objects.values_list("session_id", flat=True).get(
        pk=attendance_id
    )
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        attendance = (
            Attendance.objects.select_for_update()
            .select_related("person")
            .get(pk=attendance_id)
        )
        if (
            not scope_records(Attendance.objects.all(), user, "edit_records")
            .filter(pk=attendance.pk)
            .exists()
        ):
            raise PermissionDenied
        if attendance.person_id == person_id_for(user):
            raise PermissionDenied(
                "You cannot assess or issue a credential to yourself."
            )
        if (
            attendance.status != "completed"
            or not attendance.check_out_at
            or session.status == "cancelled"
            or attendance.person.is_anonymised
            or attendance.person.merged_into_id
        ):
            raise ValidationError(
                "Awards require completed active participant attendance."
            )
        if attendance.import_batch_id and attendance.import_batch.status != "committed":
            raise ValidationError(
                "This attendance source import is no longer committed."
            )
        if (
            not session.competencies.filter(competency=competency).exists()
            or not session.programme.competency_targets.filter(
                competency=competency
            ).exists()
        ):
            raise ValidationError(
                "Map the competency to both this workshop and programme."
            )
        if (
            not competency.framework.is_knust
            or competency.status not in {"active", "published"}
            or competency.framework.status not in {"active", "published"}
            or competency.domain.status not in {"active", "published"}
        ):
            raise ValidationError("Choose an active KNUST competency.")
        if (
            level.framework_id != competency.framework_id
            or level.ordinal > evidence_cap(competency, evidence_type)
        ):
            raise ValidationError(
                "The level is outside the framework or evidence-rule cap."
            )
        existing = InternalAssessment.objects.filter(
            attendance=attendance, competency=competency, evidence_type=evidence_type
        ).first()
        if existing:
            if existing.level_id != level.pk or existing.note != note.strip():
                raise ValidationError(
                    "This source already has an assessment. Reconcile rather than overwrite it."
                )
            return existing, False
        credential = Credential.objects.create(
            person=attendance.person,
            kind="knust_badge",
            source="staff_recorded",
            issuer="KNUST",
            title=f"{competency.code}: {competency.title}"[:200],
            credential_id=f"workshop-{attendance.pk}-{competency.pk}-{evidence_type}",
            issued_at=timezone.localdate(),
            review_status="approved",
            reviewed_at=timezone.now(),
            reviewer=user,
            review_note=note.strip(),
        )
        evidence = Evidence.objects.create(
            person=attendance.person,
            competency=competency,
            level=level,
            type=evidence_type,
            source_type="attendance",
            source_id=attendance.pk,
            session=session,
            assessed_by=user,
            framework_version=framework_version_label(competency.framework),
        )
        award = InternalAssessment.objects.create(
            attendance=attendance,
            competency=competency,
            level=level,
            evidence_type=evidence_type,
            assessor=user,
            note=note.strip(),
            evidence=evidence,
            credential=credential,
        )
        badge = generate_badge_json(attendance.person, competency, level)
        credential.badge_json = {"jwt": badge} if badge else None
        credential.save(update_fields=["badge_json"])
        CredentialReview.objects.create(
            credential=credential,
            from_status="",
            to_status="approved",
            actor=user,
            comment=note.strip(),
        )
        log_event(
            request,
            "internal_credential_issued",
            "Credential",
            credential.pk,
            after={
                "attendance_id": attendance.pk,
                "competency_id": competency.pk,
                "level": level.ordinal,
                "assessor_id": user.pk,
                "reason": note.strip(),
            },
        )
        return award, True


def generate_credential_evidence(credential):
    """Idempotent repeated approval with framework-specific levels."""
    if (
        credential.review_status != "approved"
        or credential.status != "valid"
        or (credential.expires_at and credential.expires_at < timezone.localdate())
    ):
        return 0
    catalogue = (
        credential.catalogue
        or CertificationCatalogue.objects.filter(
            issuer__iexact=credential.issuer,
            title__iexact=credential.title,
            active=True,
        ).first()
    )
    if not catalogue or not catalogue.active:
        return 0
    written = 0
    with transaction.atomic():
        credential = Credential.objects.select_for_update().get(pk=credential.pk)
        if credential.review_status != "approved" or credential.status != "valid":
            return 0
        for mapping in catalogue.competencies.select_related("competency__framework"):
            comp = mapping.competency
            if (
                not comp.framework.is_knust
                or comp.status not in {"active", "published"}
                or comp.framework.status not in {"active", "published"}
                or comp.domain.status not in {"active", "published"}
            ):
                continue
            caps = EvidenceRule.objects.filter(
                Q(competency=comp) | Q(competency__isnull=True),
                evidence_type="external",
                status__in=["active", "published"],
            ).values_list("max_level", flat=True)
            cap = min([mapping.max_level, *caps])
            level = Level.objects.filter(framework=comp.framework, ordinal=cap).first()
            if level is None:
                continue
            evidence, created = Evidence.objects.get_or_create(
                credential=credential,
                competency=comp,
                type="external",
                source_type="credential",
                defaults={
                    "person": credential.person,
                    "level": level,
                    "source_id": credential.pk,
                    "expires_at": credential.expires_at,
                    "framework_version": framework_version_label(comp.framework),
                },
            )
            if not created:
                evidence.status = "valid"
                evidence.is_expired = False
                evidence.level = level
                evidence.expires_at = credential.expires_at
                evidence.save(
                    update_fields=["status", "is_expired", "level", "expires_at"]
                )
            written += 1
    return written
