"""Import views - upload, preview, and commit attendance spreadsheets."""

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.models import UserAccount
from apps.accounts.permissions import get_user_scopes, is_super_admin
from apps.registry.models import Person
from apps.notifications.services import send_passport_link_to_participant
from apps.passport.linking import issue_passport_link
from apps.sessions.models import (
    Attendance,
    ImportBatch,
    ImportMappingTemplate,
    Session,
)


def _manageable_sessions(user):
    """Limit attendance imports to sessions covered by operational roles."""
    if is_super_admin(user):
        return Session.objects.all()

    scopes = get_user_scopes(user)
    allowed = Q(pk__in=[])

    for scope in scopes:
        if (
            scope.role == "programme_owner"
            and scope.scope_type == "programme"
            and scope.scope_id is not None
        ):
            allowed |= Q(programme_id=scope.scope_id)
        elif (
            scope.role == "focal_person"
            and scope.scope_type == "unit"
            and scope.scope_id is not None
        ):
            allowed |= Q(host_unit_id=scope.scope_id)
        elif (
            scope.role in ("facilitator", "associate_trainer")
            and scope.scope_type == "session"
            and scope.scope_id is not None
        ):
            allowed |= Q(pk=scope.scope_id)

    if any(
        scope.role in ("facilitator", "associate_trainer")
        for scope in scopes
    ):
        try:
            person_id = user.hub_account.person_id
        except (AttributeError, UserAccount.DoesNotExist):
            person_id = None
        if person_id:
            allowed |= Q(facilitators__person_id=person_id)

    return Session.objects.filter(allowed).distinct()


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
            email_col = mapping_template.column_map.get("email")
            name_col = mapping_template.column_map.get("name")
        else:
            email_col = request.POST.get("email_col")
            name_col = request.POST.get("name_col")

        if (
            not email_col
            or not name_col
            or email_col not in columns
            or name_col not in columns
            or email_col == name_col
        ):
            messages.error(request, "Please map both Email and Full Name columns.")
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
                column_map={"email": email_col, "name": name_col},
                created_by=request.user,
            )

        # Process the preview
        preview_rows = []
        seen_emails = set()
        for row in data:
            email = _clean_import_cell(row.get(email_col, ""))
            name = _clean_import_cell(row.get(name_col, ""))
            note = ""
            person = None

            if not email or not name:
                status = "error"
            else:
                try:
                    validate_email(email)
                except ValidationError:
                    status = "error"
                else:
                    email_key = email.casefold()
                    if email_key in seen_emails:
                        status = "conflict"
                        note = "Email is duplicated in this file"
                    else:
                        seen_emails.add(email_key)
                        matches = list(
                            Person.objects.filter(email__iexact=email).order_by("id")[:2]
                        )
                        if len(matches) > 1:
                            status = "conflict"
                            note = "Email matches multiple people in the registry"
                        elif matches:
                            status = "matched"
                            person = matches[0]
                        else:
                            status = "new"

            if status == "error":
                note = "Name or valid email is missing"

            preview_rows.append({
                "email": email,
                "name": name,
                "status": status,
                "person": person,
                "note": note,
            })

        # Save the preview to session so commit can use it
        request.session["import_preview"] = [
            {
                "email": r["email"],
                "name": r["name"],
                "status": r["status"],
                "note": r["note"],
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
        "sample_rows": sample_rows,
        "total_rows": total_rows,
        "mapping_templates": ImportMappingTemplate.objects.filter(
            created_by=request.user
        ),
    })
    


@login_required
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
        batch = ImportBatch.objects.create(
            session=session,
            created_by=request.user,
            status="committed",
            counts={"total": len(preview)}
        )

        added_count = 0
        imported_people = []
        for row in preview:
            if row["status"] not in ("new", "matched"):
                continue

            person = Person.objects.filter(email__iexact=row["email"]).first()
            if not person:
                person = Person.objects.create(
                    affiliation="staff",  # default
                    email=row["email"],
                    full_name=row["name"],
                )

            attendance, created = Attendance.objects.get_or_create(
                session=session,
                person=person,
                defaults={
                    "method": "import",
                    "check_in_at": session.start_at if session_is_closed else timezone.now(),
                    "check_out_at": session.end_at if session_is_closed else None,
                    "status": "completed" if session_is_closed else "partial",
                    "import_batch": batch,
                }
            )
            if created:
                added_count += 1
                imported_people.append(person)

    for person in imported_people:
        passport_url = issue_passport_link(person)
        send_passport_link_to_participant(
            person=person,
            session_title=session.title,
            passport_url=passport_url,
        )

    # Clean up session data
    request.session.pop("import_session_id", None)
    request.session.pop("import_columns", None)
    request.session.pop("import_data", None)
    request.session.pop("import_preview", None)

    messages.success(request, f"Successfully imported {added_count} new attendance records.")
    return redirect("session_console", session_id=session.id)
