"""Import views - upload, preview, and commit attendance spreadsheets."""

import logging

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from apps.audit.services import log_event
from apps.imports.services import undo_import
from apps.accounts.permissions import scope_records

from apps.accounts.permissions import (
    can_import_person,
    can_view_person,
    require_capability,
    session_queryset,
)
from apps.registry.identifiers import normalize_name
from apps.registry.identity import (
    IdentityMatch,
    identity_data,
    match_identity,
    organisation_for_name,
    queue_identity,
    resolve_or_create,
)
from apps.registry.models import Organisation
from apps.registry.workflows import apply_import
from apps.sessions.models import (
    ImportBatch,
    ImportMappingTemplate,
    Session,
)
from apps.sessions.services import send_passport_update

logger = logging.getLogger(__name__)


def _manageable_sessions(user):
    return session_queryset(user, "import_attendance")


def _clean_import_cell(value):
    """Convert blank spreadsheet cells and scalar values safely to text."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


@login_required
@require_capability("import_attendance")
def import_upload(request):
    """Step 1: Select a session and upload a file."""
    if request.method == "POST":
        session_id = request.POST.get("session")
        file = request.FILES.get("file")

        if not session_id or not file:
            messages.error(request, "Please select a session and upload a file.")
            return render(
                request,
                "imports/upload.html",
                {"sessions": _manageable_sessions(request.user)},
            )

        session = get_object_or_404(
            _manageable_sessions(request.user), id=session_id
        )

        try:
            if file.name.endswith(".csv"):
                df = pd.read_csv(file)
            elif file.name.endswith(".xlsx"):
                df = pd.read_excel(file)
            else:
                messages.error(request, "Please upload a .csv or .xlsx file.")
                return redirect("import_upload")
        except Exception as e:
            logger.exception("Failed to parse the uploaded attendance file.")
            messages.error(request, f"Failed to read file: {e}")
            return redirect("import_upload")

        # Store the dataframe in the session (or a temp file) for the preview step
        # For simplicity, we'll pass the columns to the preview page and re-read the file on commit.
        # In a production system, you would save the file to disk or S3 here.
        
        columns = df.columns.tolist()
        request.session["import_session_id"] = session.id
        request.session["import_columns"] = columns
        request.session["import_data"] = df.to_dict(orient="records")  # Store data in session for demo

        return redirect("import_preview")

    sessions = _manageable_sessions(request.user).order_by("-start_at")
    return render(request, "imports/upload.html", {"sessions": sessions})


@login_required
@require_capability("import_attendance")
def import_preview(request):
    """Step 2: Map columns and preview the result."""
    session_id = request.session.get("import_session_id")
    columns = request.session.get("import_columns", [])
    data = request.session.get("import_data", [])

    if not session_id or not data:
        messages.error(request, "No import data found. Please upload a file first.")
        return redirect("import_upload")

    session = get_object_or_404(
        _manageable_sessions(request.user), id=session_id
    )
    if request.method == "POST":
        mapping_template_id = request.POST.get("mapping_template", "").strip()
        if mapping_template_id:
            mapping_template = get_object_or_404(
                ImportMappingTemplate,
                id=mapping_template_id,
                created_by=request.user,
            )
            mapping = dict(mapping_template.column_map)
        else:
            mapping = {key: request.POST.get(f"{key}_col") for key in ("email", "name", "number", "phone", "affiliation", "organisation")}
        email_col, name_col = mapping.get("email"), mapping.get("name")
        chosen = [column for column in mapping.values() if column]
        if (
            not name_col or not any(mapping.get(key) for key in ("email", "number", "phone", "organisation"))
            or any(column not in columns for column in chosen) or len(set(chosen)) != len(chosen)
        ):
            messages.error(request, "Map Full Name and at least one identity column using distinct file columns.")
            return redirect("import_preview")

        if request.POST.get("save_mapping"):
            mapping_name = request.POST.get("mapping_name", "").strip()
            if not mapping_name or len(mapping_name) > 100:
                messages.error(
                    request,
                    "Enter a mapping name of 1 to 100 characters to save it.",
                )
                return redirect("import_preview")
            ImportMappingTemplate.objects.create(
                name=mapping_name,
                column_map={key: value for key, value in mapping.items() if value},
                created_by=request.user,
            )

        # Process the preview
        preview_rows = []
        seen = {}
        for row in data:
            raw = {key: _clean_import_cell(row.get(column, "")) if column else "" for key, column in mapping.items()}
            email, name = raw.get("email", ""), raw.get("name", "")
            person, submitted, duplicate_exact = None, None, False
            status, note = "error", "Name or valid identity is missing"
            try:
                if not name:
                    raise ValidationError("A full name is required.")
                org_name = raw.get("organisation", "")
                org_ids = tuple(Organisation.objects.filter(name_key=normalize_name(org_name)).values_list("pk", flat=True)[:2]) if org_name else ()
                submitted = identity_data(
                    full_name=name, email=email, phone=raw.get("phone", ""),
                    staff_or_student_no=raw.get("number", ""), affiliation=raw.get("affiliation") or None,
                    organisation_id=org_ids[0] if len(org_ids) == 1 else None, organisation_name=org_name,
                )
                result = match_identity(submitted, source="import") if len(org_ids) < 2 else IdentityMatch("conflict", submitted, reason="Organisation name is ambiguous.", organisation_ids=org_ids)
                person, status, note = result.person, result.status, result.reason
                key = submitted["staff_or_student_no"] or submitted["email"] or submitted["phone"] or f"{normalize_name(name)}:{normalize_name(org_name)}"
                if key in seen:
                    duplicate_exact = seen[key] == submitted
                    status = "conflict"
                    note = "Email is duplicated in this file" if submitted["email"] else "Identifier is duplicated in this file"
                else:
                    seen[key] = submitted
                if result.status == "conflict" and result.reason.startswith("More than one person"):
                    note = "Email matches multiple people in the registry"
                if status in {"new", "matched"} and not can_import_person(request.user, session, person):
                    status, note = "error", "Participant is outside your assigned unit."
            except ValidationError as exc:
                status, note = "error", "; ".join(exc.messages)
            preview_rows.append({
                "email": submitted["email"] if submitted else email, "name": name,
                "status": status, "note": note, "identity": submitted, "duplicate_exact": duplicate_exact,
                "person": person if person and can_view_person(request.user, person.pk) else None,
            })

        # Save the preview to session so commit can use it
        request.session["import_preview"] = [
            {
                "email": r["email"],
                "name": r["name"],
                "status": r["status"],
                "note": r["note"],
                "identity": r["identity"],
                "duplicate_exact": r["duplicate_exact"],
            }
            for r in preview_rows
        ]

        new_count = sum(1 for r in preview_rows if r["status"] == "new")
        matched_count = sum(1 for r in preview_rows if r["status"] == "matched")
        conflict_count = sum(1 for r in preview_rows if r["status"] == "conflict")
        error_count = sum(1 for r in preview_rows if r["status"] == "error")

        return render(request, "imports/preview.html", {
            "session": session,
            "preview_rows": preview_rows,
            "email_col": email_col,
            "name_col": name_col,
            "new_count": new_count,
            "matched_count": matched_count,
            "conflict_count": conflict_count,
            "error_count": error_count,
        })

 
     # Build a small sample of rows for display in the mapper
    sample_rows = data[:3] if data else []
    total_rows = len(data)

    return render(request, "imports/map_columns.html", {
        "session": session,
        "columns": columns,
        "extra_mappings": [("number", "Staff/student number"), ("phone", "Phone"), ("affiliation", "Affiliation: staff/student/external"), ("organisation", "Organisation name")],
        "sample_rows": sample_rows,
        "total_rows": total_rows,
        "mapping_templates": ImportMappingTemplate.objects.filter(
            created_by=request.user
        ),
    })
    


@login_required
@require_capability("import_attendance")
def import_commit(request):
    """Step 3: Commit the import."""
    if request.method != "POST":
        return redirect("import_upload")

    session_id = request.session.get("import_session_id")
    preview = request.session.get("import_preview", [])
    
    if not session_id or not preview:
        messages.error(request, "No import data to commit.")
        return redirect("import_upload")

    session = get_object_or_404(
        _manageable_sessions(request.user), id=session_id
    )
    session_is_closed = session.status == "closed"

    with transaction.atomic():
        session = Session.objects.select_for_update().get(pk=session.pk)
        session_is_closed = session.status == "closed"
        batch = ImportBatch.objects.create(
            session=session,
            created_by=request.user,
            status="committed",
            counts={"total": len(preview)}
        )

        added_count = 0
        conflict_count, error_count = 0, 0
        imported_people = []
        context = {
            "batch_id": batch.pk, "status": "completed" if session_is_closed else "partial",
            "check_in_at": (session.start_at if session_is_closed else timezone.now()).isoformat(),
            "check_out_at": session.end_at.isoformat() if session_is_closed else "",
        }
        for row in preview:
            if row["status"] == "error" or row.get("duplicate_exact"):
                continue
            try:
                with transaction.atomic():
                    submitted = dict(row.get("identity") or identity_data(email=row["email"], full_name=row["name"]))
                    org_name = submitted.get("organisation_name", "")
                    org, org_ids = organisation_for_name(org_name) if org_name else (None, ())
                    if org:
                        submitted["organisation_id"] = org.pk
                    if org_ids:
                        result = queue_identity(IdentityMatch("conflict", submitted, reason="Organisation name is ambiguous.", organisation_ids=org_ids), source="import", session=session, context=context, user=request.user)
                    else:
                        result = resolve_or_create(submitted, source="import", session=session, context=context, user=request.user)
                    if result.status == "conflict":
                        conflict_count += 1
                        continue
                    if result.status == "error":
                        raise ValidationError(result.reason)
                    person = result.person
                    if not can_import_person(request.user, session, person):
                        from django.core.exceptions import PermissionDenied
                        raise PermissionDenied
                    _attendance, created = apply_import(person, session, context)
                    if created:
                        added_count += 1
                        imported_people.append(person)
            except ValidationError:
                error_count += 1
        batch.refresh_from_db(fields=["counts"])
        batch.counts = {**batch.counts, "total": len(preview), "attendances": added_count, "conflict": conflict_count, "error": error_count}
        batch.save(update_fields=["counts"])
        log_event(request, "import_committed", "ImportBatch", batch.pk, after=batch.counts)

    for person in imported_people:
        send_passport_update(person, session.title)

    # Clean up session data
    request.session.pop("import_session_id", None)
    request.session.pop("import_columns", None)
    request.session.pop("import_data", None)
    request.session.pop("import_preview", None)

    messages.success(request, f"Successfully imported {added_count} new attendance records.")
    return redirect("session_console", session_id=session.id)


@login_required
@require_capability("import_attendance")
def import_history(request, session_id):
    session = get_object_or_404(_manageable_sessions(request.user), pk=session_id)
    return render(request, "imports/history.html", {"session": session, "batches": session.import_batches.select_related("created_by", "undone_by")})


@login_required
@require_capability("import_attendance")
def import_detail(request, batch_id):
    batch = get_object_or_404(scope_records(ImportBatch.objects.all(), request.user, "import_attendance"), pk=batch_id)
    from apps.sessions.models import Attendance
    rows = scope_records(Attendance.objects.filter(import_batch=batch), request.user, "import_attendance")
    from apps.passport.models import Evidence
    visible_ids = rows.values("pk")
    impact = {"attendance": rows.count(), "evidence": Evidence.objects.filter(type="attendance", source_type="attendance", source_id__in=visible_ids, status="valid").count(), "partial_scope": batch.attendances.exclude(pk__in=visible_ids).exists()}
    return render(request, "imports/detail.html", {"batch": batch, "attendances": rows.select_related("person"), "impact": impact})


@login_required
@require_capability("import_attendance")
@require_POST
def import_undo(request, batch_id):
    batch = get_object_or_404(scope_records(ImportBatch.objects.all(), request.user, "import_attendance"), pk=batch_id)
    try:
        _, changed = undo_import(batch.pk, request.user, reason=request.POST.get("reason", ""), request=request)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Import withdrawn; people and history retained." if changed else "This import was already undone.")
    return redirect("import_detail", batch_id=batch.pk)
