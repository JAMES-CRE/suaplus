"""Apply accepted identities and resolve pending submissions without merging people."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.permissions import (
    can,
    can_import_person,
    is_super_admin,
    person_queryset,
    scopes_for,
    session_queryset,
)
from apps.audit.services import log_event
from apps.registry.identity import (
    canonical_person,
    create_person,
    identity_lock,
    match_identity,
)
from apps.registry.models import Consent, IdentityConflict, Person
from apps.sessions.models import Attendance, ImportBatch, Session
from apps.sessions.services import grant_attendance_evidence


def apply_checkin(person, session, context):
    """Create one attendance and retain the consent actually submitted."""
    minor = person.age_band == "under_18"
    if minor and not (context.get("guardian_consent") and context.get("guardian_name") and context.get("guardian_contact")):
        raise ValidationError("Guardian consent and contact details are required for this participant.")
    if not minor and not context.get("consent"):
        raise ValidationError("Data-processing consent is required.")
    submitted = parse_datetime(context.get("submitted_at", "")) or timezone.now()
    attendance, created = Attendance.objects.get_or_create(
        person=person, session=session,
        defaults={"method": "code", "status": "partial", "check_in_at": submitted},
    )
    if not created:
        return attendance, False
    entries = [("data_processing", context["guardian_name"] if minor else person.full_name, "")]
    if minor:
        entries.append(("guardian", context["guardian_name"], context["guardian_contact"]))
    if context.get("sms_consent") and person.phone and not minor:
        entries.append(("sms", person.full_name, ""))
    for kind, given_by, contact in entries:
        consent = Consent.objects.create(person=person, type=kind, given_by=given_by, contact_details=contact, notice_version="1.0")
        Consent.objects.filter(pk=consent.pk).update(given_at=submitted)
    return attendance, True


def apply_import(person, session, context):
    if person.age_band == "under_18" and not person.consents.filter(type__in=["guardian", "school"], withdrawn_at__isnull=True).exists():
        raise ValidationError("Minor imports require an existing guardian or school consent record.")
    completed = context.get("status") == "completed"
    check_in = parse_datetime(context.get("check_in_at", "")) or timezone.now()
    check_out = parse_datetime(context.get("check_out_at", "")) if completed else None
    batch = ImportBatch.objects.select_for_update().get(pk=context["batch_id"], session=session)
    if batch.status != "committed":
        raise ValidationError("This import batch is no longer committed.")
    attendance, created = Attendance.objects.get_or_create(
        person=person, session=session,
        defaults={"method": "import", "status": "completed" if completed else "partial", "check_in_at": check_in, "check_out_at": check_out, "import_batch": batch},
    )
    if created:
        sources = dict(batch.counts.get("attendance_sources", {}))
        sources[str(attendance.pk)] = {"status": attendance.status, "check_in_at": attendance.check_in_at.isoformat() if attendance.check_in_at else None, "check_out_at": attendance.check_out_at.isoformat() if attendance.check_out_at else None}
        batch.counts = {**batch.counts, "attendance_sources": sources}
        batch.save(update_fields=["counts"])
    if created and completed:
        grant_attendance_evidence(attendance)
    return attendance, created


def conflict_queryset(user):
    qs = IdentityConflict.objects.all()
    if is_super_admin(user):
        return qs
    if not can(user, "edit_records"):
        return qs.none()
    session_ids = session_queryset(user, "edit_records").values("pk")
    qs = qs.filter(session_id__in=session_ids)
    # A focal person's college scope must not expose incoming identities from
    # other colleges simply because the workshop is hosted in their unit.
    roles = scopes_for(user, "edit_records")
    if not any(scope.role in {"programme_owner", "facilitator"} for scope in roles):
        allowed = person_queryset(user, "edit_people").values("pk")
        qs = qs.filter(candidates__in=allowed).exclude(candidates__in=Person.objects.exclude(pk__in=allowed))
    return qs.distinct()


def visible_candidates(conflict, user):
    candidates = Person.objects.filter(pk__in=conflict.candidates.values("pk"))
    if is_super_admin(user):
        return candidates
    return candidates.filter(pk__in=person_queryset(user).values("pk"))


def _registry_update(person, values):
    from apps.registry.identifiers import normalize_email, normalize_number

    for field, normalizer, key in (
        ("email", normalize_email, "identity_email"),
        ("staff_or_student_no", normalize_number, "identity_number"),
    ):
        value = normalizer(values.get(field, ""))
        if not value:
            continue
        for candidate in Person.objects.filter(**{key: value}, is_anonymised=False).exclude(pk=person.pk):
            canonical = canonical_person(candidate)
            if canonical is None or canonical.pk != person.pk:
                raise ValidationError("The Registry identifier belongs to another participant. Reconcile the records before applying this update.")
    for field, value in values.items():
        if field not in {"email", "full_name", "affiliation", "staff_or_student_no", "category", "rank_or_level", "unit_id"}:
            raise ValidationError("Unsupported Registry field.")
        setattr(person, field, value)
    person.full_clean()
    person.save()


def resolve_conflict(conflict_id, user, *, action, person_id=None, organisation_id=None, note="", request=None):
    if not can(user, "edit_records"):
        raise PermissionDenied
    if action not in {"match", "create", "reject"} or not note.strip() or len(note) > 2000:
        raise ValidationError("Choose a resolution and give a note of 1–2,000 characters.")
    conflict = conflict_queryset(user).get(pk=conflict_id)
    data = dict(conflict.payload["identity"])
    if action == "create" and organisation_id is not None and conflict.organisation_candidates.filter(pk=organisation_id).exists():
        data["organisation_id"] = organisation_id
    with transaction.atomic():
        if conflict.session_id:
            Session.objects.select_for_update().get(pk=conflict.session_id)
        with identity_lock(data):
            conflict = IdentityConflict.objects.select_for_update().get(pk=conflict_id)
            if not conflict_queryset(user).filter(pk=conflict.pk).exists():
                raise PermissionDenied
            if conflict.status != "pending":
                raise ValidationError("This submission has already been resolved.")
            all_ids = set(conflict.candidates.values_list("pk", flat=True))
            visible_ids = set(visible_candidates(conflict, user).values_list("pk", flat=True))
            if action != "reject" and not is_super_admin(user) and all_ids != visible_ids:
                raise PermissionDenied("An administrator must review candidate records outside your scope.")
            if action == "reject":
                person = None
                status = "rejected"
            elif action == "match":
                person = Person.objects.filter(pk=person_id).first()
                person = canonical_person(person) if person else None
                stored = {canonical_person(candidate).pk for candidate in conflict.candidates.all() if canonical_person(candidate) is not None}
                fresh = match_identity(data, source=conflict.source)
                allowed_ids = stored | set(fresh.candidate_ids)
                if person is None or person.pk not in allowed_ids:
                    raise ValidationError("Select one of this submission's active candidate identities.")
                if not is_super_admin(user) and not person_queryset(user).filter(pk=person.pk).exists():
                    raise PermissionDenied
                status = "matched"
            else:
                if data["staff_or_student_no"] and Person.objects.filter(identity_number=data["staff_or_student_no"], is_anonymised=False).exists():
                    raise ValidationError("An existing staff/student number cannot be duplicated. Match or reconcile it first.")
                if data["affiliation"] != "external" and not data["staff_or_student_no"]:
                    raise ValidationError("A distinct internal participant requires a staff/student number.")
                if conflict.organisation_candidates.exists():
                    organisation = conflict.organisation_candidates.filter(pk=organisation_id).first()
                    if organisation is None:
                        raise ValidationError("Select the correct organisation for this identity.")
                    data["organisation_id"] = organisation.pk
                elif organisation_id is not None:
                    raise ValidationError("The organisation is not one of this submission's candidates.")
                person = create_person(data)
                status = "created"
            context = conflict.payload.get("context", {})
            attendance = None
            attendance_created = False
            if person:
                if conflict.session_id and not can_import_person(user, conflict.session, person):
                    raise PermissionDenied
                if conflict.source == "checkin":
                    if conflict.session.status not in {"scheduled", "open"}:
                        raise ValidationError("The session is no longer open for check-in.")
                    attendance, attendance_created = apply_checkin(person, conflict.session, context)
                elif conflict.source == "import":
                    attendance, attendance_created = apply_import(person, conflict.session, context)
                elif conflict.source == "sas":
                    if not is_super_admin(user):
                        raise PermissionDenied
                    from apps.passport.sas_views import send_self_assessment_link
                    transaction.on_commit(lambda: send_self_assessment_link(person))
                elif conflict.source == "registry":
                    if not is_super_admin(user):
                        raise PermissionDenied
                    _registry_update(person, context["values"])
                elif conflict.source == "account":
                    from allauth.account.models import EmailAddress

                    from apps.accounts.models import UserAccount
                    account = UserAccount.objects.select_for_update().get(user_id=conflict.submitted_by_id)
                    if not EmailAddress.objects.filter(user_id=conflict.submitted_by_id, email__iexact=data["email"], verified=True).exists():
                        raise ValidationError("The account must verify its email before it can be linked.")
                    if account.person_id is not None or UserAccount.objects.filter(person=person).exclude(pk=account.pk).exists():
                        raise ValidationError("An existing account association must be reconciled by an administrator.")
                    account.person = person
                    account.save(update_fields=["person", "updated_at"])
            conflict.status = status
            conflict.selected_person = person
            conflict.resolved_by = user
            conflict.resolved_at = timezone.now()
            conflict.resolution_note = note.strip()
            conflict.save(update_fields=["status", "selected_person", "resolved_by", "resolved_at", "resolution_note"])
            log_event(request, "identity_resolved", "IdentityConflict", conflict.pk,
                      before={"status": "pending"}, after={"status": status, "person_id": person.pk if person else None, "note": conflict.resolution_note})
            return conflict, attendance, attendance_created
