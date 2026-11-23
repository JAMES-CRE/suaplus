"""All-or-nothing reversal without deleting participant history."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.accounts.permissions import (
    can_import_person,
    is_super_admin,
    person_queryset,
    scopes_for,
    session_queryset,
)
from apps.audit.services import log_event
from apps.passport.models import Evidence
from apps.registry.models import IdentityConflict
from apps.sessions.models import Attendance, ImportBatch, Session


def undo_import(batch_id, user, *, reason, request=None):
    reason = reason.strip()
    if not reason or len(reason) > 2000:
        raise ValidationError("Give an undo reason of 1–2,000 characters.")
    session_id = ImportBatch.objects.values_list("session_id", flat=True).get(
        pk=batch_id
    )
    if not session_queryset(user, "import_attendance").filter(pk=session_id).exists():
        raise PermissionDenied
    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session_id)
        batch = ImportBatch.objects.select_for_update().get(pk=batch_id)
        rows = list(
            Attendance.objects.select_for_update()
            .filter(import_batch=batch)
            .select_related("person")
        )
        if any(not can_import_person(user, session, row.person) for row in rows):
            raise PermissionDenied(
                "An administrator must reverse participants outside your scope."
            )
        if batch.status == "undone":
            return batch, False
        if batch.status != "committed":
            raise ValidationError("Only committed imports can be undone.")
        ids = [row.pk for row in rows]
        snapshots = batch.counts.get("attendance_sources", {})
        for row in rows:
            original = snapshots.get(str(row.pk))
            current = {
                "status": row.status,
                "check_in_at": row.check_in_at.isoformat() if row.check_in_at else None,
                "check_out_at": row.check_out_at.isoformat()
                if row.check_out_at
                else None,
            }
            if original and original != current:
                raise ValidationError(
                    "Attendance changed after import. Reconcile later completion before undo."
                )
        from apps.audit.models import AuditEvent

        if AuditEvent.objects.filter(
            entity="Attendance",
            entity_id__in=[str(pk) for pk in ids],
            at__gt=batch.created_at,
            action="attendance_confirmed",
        ).exists():
            raise ValidationError(
                "A later staff completion depends on this import. Reconcile it first."
            )
        if any(row.internal_assessments.exists() for row in rows):
            raise ValidationError(
                "Internal credentials depend on this batch. Reconcile them first."
            )
        if (
            any(row.survey_responses.exists() for row in rows)
            or Evidence.objects.filter(source_type="attendance", source_id__in=ids)
            .exclude(type="attendance")
            .exists()
        ):
            raise ValidationError(
                "Later checkout or assessment records exist. Reconcile them before undoing the import."
            )
        if any(row.method != "import" for row in rows):
            raise ValidationError(
                "Attendance provenance changed; administrator reconciliation is required."
            )
        pending = IdentityConflict.objects.select_for_update().filter(
            source="import",
            session=session,
            status="pending",
            payload__context__batch_id=batch.pk,
        )
        if not is_super_admin(user) and not any(
            s.role in {"programme_owner", "facilitator"}
            for s in scopes_for(user, "import_attendance")
        ):
            allowed = person_queryset(user).values("pk")
            from apps.registry.models import Person

            if (
                pending.filter(
                    candidates__in=Person.objects.exclude(pk__in=allowed)
                ).exists()
                or pending.filter(candidates__isnull=True).exists()
            ):
                raise PermissionDenied(
                    "An administrator must review this batch’s pending identities."
                )
        Attendance.objects.filter(pk__in=ids).update(status="withdrawn")
        revoked = Evidence.objects.filter(
            type="attendance",
            source_type="attendance",
            source_id__in=ids,
            status="valid",
        ).update(status="revoked")
        rejected = pending.update(
            status="rejected",
            resolved_by=user,
            resolved_at=timezone.now(),
            resolution_note=f"Import undone: {reason}",
        )
        batch.status = "undone"
        batch.undone_at = timezone.now()
        batch.undone_by = user
        batch.undo_reason = reason
        batch.save(update_fields=["status", "undone_at", "undone_by", "undo_reason"])
        log_event(
            request,
            "import_undone",
            "ImportBatch",
            batch.pk,
            before={"status": "committed"},
            after={
                "status": "undone",
                "reason": reason,
                "actor_id": user.pk,
                "withdrawn": len(ids),
                "revoked_evidence": revoked,
                "rejected_pending": rejected,
            },
        )
        return batch, True
