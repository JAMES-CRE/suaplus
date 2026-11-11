"""Import views - upload, preview, and commit attendance spreadsheets."""

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from apps.registry.models import Person
from apps.sessions.models import Attendance, ImportBatch, Session


@login_required
def import_upload(request):
    """Step 1: Select a session and upload a file."""
    if request.method == "POST":
        session_id = request.POST.get("session")
        file = request.FILES.get("file")

        if not session_id or not file:
            messages.error(request, "Please select a session and upload a file.")
            return render(request, "imports/upload.html", {"sessions": Session.objects.all()})

        session = get_object_or_404(Session, id=session_id)

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

    sessions = Session.objects.order_by("-start_at")
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

    session = get_object_or_404(Session, id=session_id)

    if request.method == "POST":
        # Get the column mapping from the form
        email_col = request.POST.get("email_col")
        name_col = request.POST.get("name_col")

        if not email_col or not name_col:
            messages.error(request, "Please map both Email and Full Name columns.")
            return redirect("import_preview")

        # Process the preview
        preview_rows = []
        for row in data:
            email = str(row.get(email_col, "")).strip()
            name = str(row.get(name_col, "")).strip()
            
            if not email or pd.isna(email) or email == "nan":
                status = "error"
                person = None
            else:
                person = Person.objects.filter(email__iexact=email).first()
                status = "matched" if person else "new"

            preview_rows.append({
                "email": email,
                "name": name,
                "status": status,
                "person": person
            })

        # Save the preview to session so commit can use it
        request.session["import_preview"] = [
            {"email": r["email"], "name": r["name"], "status": r["status"]} for r in preview_rows
        ]

        new_count = sum(1 for r in preview_rows if r["status"] == "new")
        matched_count = sum(1 for r in preview_rows if r["status"] == "matched")
        error_count = sum(1 for r in preview_rows if r["status"] == "error")

        return render(request, "imports/preview.html", {
            "session": session,
            "preview_rows": preview_rows,
            "email_col": email_col,
            "name_col": name_col,
            "new_count": new_count,
            "matched_count": matched_count,
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

    session = get_object_or_404(Session, id=session_id)

    with transaction.atomic():
        batch = ImportBatch.objects.create(
            session=session,
            created_by=request.user,
            status="committed",
            counts={"total": len(preview)}
        )

        added_count = 0
        for row in preview:
            if row["status"] == "error":
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
                    "check_in_at": session.start_at,
                    "check_out_at": session.end_at,
                    "status": "completed",
                    "import_batch": batch,
                }
            )
            if created:
                added_count += 1

    # Clean up session data
    request.session.pop("import_session_id", None)
    request.session.pop("import_columns", None)
    request.session.pop("import_data", None)
    request.session.pop("import_preview", None)

    messages.success(request, f"Successfully imported {added_count} new attendance records.")
    return redirect("session_console", session_id=session.id)