"""Public check-in and check-out views."""

import hashlib
import hmac
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import (
    can,
    person_id_for,
    programmes_for_creation,
    require_capability,
    scope_records,
    session_queryset,
)
from apps.audit.services import log_event
from apps.core.rate_limit import rate_limit
from apps.library.models import SurveyTemplate
from apps.registry.identity import (
    IdentityMatch,
    identity_data,
    match_identity,
    organisation_for_name,
    queue_identity,
    resolve_or_create,
)
from apps.registry.models import PassportToken, Person
from apps.registry.workflows import apply_checkin
from apps.sessions.forms import CheckinForm, CheckoutForm, SessionForm
from apps.sessions.models import (
    Attendance,
    Facilitation,
    Session,
    SurveyResponse,
)
from apps.sessions.qrhelper import qr_data_uri
from apps.sessions.services import complete_attendance, send_passport_update

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------







def _rotating_token(session, offset=0):
    """Return a token for the current 30-second bucket (or a past one)."""
    bucket = (int(time.time()) // 30) - offset
    msg = f"{session.id}:{bucket}".encode()
    return hmac.new(
        session.qr_secret.encode(), msg, "sha256"
    ).hexdigest()


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------


@rate_limit("checkin", max_requests=1200, window_seconds=120)
def checkin(request, code):
    """Display and process the check-in form for a session."""
    session = get_object_or_404(
        Session, session_code=code, status__in=["scheduled", "open"]
    )

    
    # Validate the rotating QR token if one was provided (SES-03)
    provided_token = request.GET.get("t", "").strip()
    if provided_token:
        # Accept the current token or the one from the previous 30s window
        current_token = _rotating_token(session)
        previous_token = _rotating_token(session, offset=1)
        
        if not (hmac.compare_digest(provided_token, current_token) or 
                hmac.compare_digest(provided_token, previous_token)):
            return render(
                request, "public/checkin_expired.html", {"session": session}
            )
    
    
    if request.method == "POST":
        form = CheckinForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data

            context = {key: data.get(key) for key in ("consent", "sms_consent", "guardian_consent", "guardian_name", "guardian_contact")}
            context["submitted_at"] = timezone.now().isoformat()
            try:
                with transaction.atomic():
                    session = get_object_or_404(Session.objects.select_for_update(), pk=session.pk, status__in=["scheduled", "open"])
                    org, org_ids = organisation_for_name(data.get("organisation_name", "")) if data["affiliation"] == "external" else (None, ())
                    submitted = identity_data(
                        full_name=data["full_name"], affiliation=data["affiliation"], email=data.get("email", ""),
                        phone=data.get("phone", ""), staff_or_student_no=data.get("staff_or_student_no", ""),
                        organisation_id=org.pk if org else None, organisation_name=data.get("organisation_name", ""), age_band=data["age_band"],
                    )
                    if org_ids:
                        match = queue_identity(IdentityMatch("conflict", submitted, reason="More than one organisation uses this name.", organisation_ids=org_ids), source="checkin", session=session, context=context)
                    else:
                        match = resolve_or_create(submitted, source="checkin", session=session, context=context)
                    if match.status == "conflict":
                        return render(request, "public/checkin_pending.html", {"session": session})
                    if match.status == "error":
                        raise ValidationError(match.reason)
                    person = match.person
                    if person.affiliation == "external" and org and person.organisation_id is None:
                        person.organisation = org
                        person.save(update_fields=["organisation"])
                    _attendance, created = apply_checkin(person, session, context)
                    if not created:
                        messages.info(request, "You were already checked in to this session.")
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return redirect("checkin_done", code=session.session_code)
    else:
        form = CheckinForm()

    return render(
        request,
        "public/checkin.html",
        {"session": session, "form": form},
    )


def checkin_done(request, code):
    """Confirmation page after successful check-in."""
    session = get_object_or_404(Session, session_code=code)
    return render(request, "public/checkin_done.html", {"session": session})


# ---------------------------------------------------------------------------
# Check-out
# ---------------------------------------------------------------------------


def checkout(request, code, person_id):
    """Complete once; replay never overwrites feedback or rotates the link."""
    session = get_object_or_404(Session, session_code=code)
    attendance = get_object_or_404(Attendance, session=session, person_id=person_id)
    if attendance.status == "completed":
        return render(request, "public/checkout_done.html", {"session": session, "already_completed": True})
    form = CheckoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            attendance, changed, evidence_count = complete_attendance(attendance.pk, checkout_data=form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            delivery_recipient = "unavailable"
            if changed:
                log_event(
                    request, "attendance_completed", "Attendance", attendance.pk,
                    before={"status": "partial", "check_out_at": None},
                    after={"status": "completed", "check_out_at": attendance.check_out_at.isoformat(), "evidence_count": evidence_count},
                )
                delivery_recipient = send_passport_update(attendance.person, session.title)
            return render(request, "public/checkout_done.html", {
                "session": session, "delivery_recipient": delivery_recipient, "already_completed": not changed,
            })
    return render(request, "public/checkout.html", {"session": session, "attendance": attendance, "form": form})


# ---------------------------------------------------------------------------
# Session management (Hub)
# ---------------------------------------------------------------------------


def _sessions_visible_to(user, capability="view_sessions"):
    return session_queryset(user, capability)


def _programmes_user_can_schedule(user):
    return programmes_for_creation(user)


@login_required
@require_capability("view_sessions")
def session_list(request):
    """List of sessions the user can see."""
    from apps.programmes.models import Programme

    visible_attendances = scope_records(Attendance.objects.all(), request.user, "view_sessions")
    qs = _sessions_visible_to(request.user).select_related("programme", "host_unit").annotate(
        attendance_count=Count("attendances", filter=Q(attendances__in=visible_attendances))
    )

    # Optional filters
    programme_id = request.GET.get("programme")
    status = request.GET.get("status")
    if programme_id:
        qs = qs.filter(programme_id=programme_id)
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-start_at")

    # Programme filter choices
    programmes = Programme.objects.filter(
        sessions__in=qs, status="active"
    ).distinct().order_by("name")

    return render(
        request,
        "sessions/session_list.html",
        {
            "sessions": qs,
            "programmes": programmes,
            "status_choices": [
                ("scheduled", "Scheduled"),
                ("open", "Open"),
                ("closed", "Closed"),
                ("cancelled", "Cancelled"),
            ],
            "selected_programme": programme_id or "",
            "selected_status": status or "",
            "count": qs.count(),
            "can_create": can(request.user, "create_sessions"),
        },
    )

@login_required
@require_capability("create_sessions")
def session_create(request):
    programmes = _programmes_user_can_schedule(request.user).filter(status="active")
    if not programmes.exists():
        messages.error(request, "Ask an administrator to assign an active programme before creating a session.")
        return redirect("session_list")
    selected = request.GET.get("programme", "")
    if selected and (not selected.isdecimal() or not programmes.filter(pk=selected).exists()):
        raise PermissionDenied
    form = SessionForm(request.POST or None, programmes=programmes, initial={"programme": selected} if selected else {})
    form.fields["programme"].widget.attrs.update({
        "hx-get": reverse("session_create"), "hx-trigger": "change", "hx-include": "this",
        "hx-target": "#session-competencies", "hx-select": "#session-competencies", "hx-swap": "outerHTML",
    })
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                session = form.save()
                form.save_mappings()
                person_id = person_id_for(request.user)
                if person_id:
                    Facilitation.objects.get_or_create(session=session, person_id=person_id, defaults={"role": "lead"})
                log_event(request, "session_created", "Session", session.pk, after={"programme_id": session.programme_id, "competencies": list(session.competencies.values_list("competency_id", flat=True))})
        except ValidationError as exc:
            form.add_error("competencies", exc)
        else:
            messages.success(request, f"Session '{session.title}' created. Session code: {session.session_code}")
            return redirect("session_console", session_id=session.pk)
    return render(request, "sessions/session_create.html", {"form": form})


@login_required
@require_capability("manage_sessions")
def session_edit(request, session_id):
    session = get_object_or_404(session_queryset(request.user, "manage_sessions"), pk=session_id)
    from apps.programmes.models import Programme
    programmes = Programme.objects.filter(pk=session.programme_id)
    form = SessionForm(request.POST or None, instance=session, programmes=programmes)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                Session.objects.select_for_update().get(pk=session.pk)
                form.save()
                form.save_mappings()
                log_event(request, "session_updated", "Session", session.pk, after={"title": session.title, "competencies": list(session.competencies.values_list("competency_id", flat=True))})
        except ValidationError as exc:
            form.add_error("competencies", exc)
        else:
            messages.success(request, "Session details updated.")
            return redirect("session_console", session_id=session.pk)
    return render(request, "sessions/session_create.html", {"form": form, "editing": True, "session": session})


@login_required
@require_capability("edit_records")
@require_POST
def attendance_confirm(request, session_id, attendance_id):
    attendance = get_object_or_404(scope_records(Attendance.objects.all(), request.user, "edit_records"), pk=attendance_id, session_id=session_id)
    reason = request.POST.get("reason", "").strip()
    if not reason or len(reason) > 1000:
        messages.error(request, "Give a confirmation reason of 1–1,000 characters.")
        return redirect("session_console", session_id=session_id)
    try:
        attendance, changed, evidence_count = complete_attendance(attendance.pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        if changed:
            log_event(request, "attendance_confirmed", "Attendance", attendance.pk,
                      before={"status": "partial"},
                      after={"status": "completed", "reason": reason, "evidence_count": evidence_count})
            send_passport_update(attendance.person, attendance.session.title)
        messages.success(request, "Attendance confirmed." if changed else "Attendance was already completed.")
    return redirect("session_console", session_id=session_id)


@login_required
@require_capability("view_sessions")
def session_console(request, session_id):
    """Live console for a session: rotating QR, roster, close button."""
    session = get_object_or_404(
        _sessions_visible_to(request.user).select_related("programme"), id=session_id
    )

    return render(
        request,
        "sessions/session_console.html",
        {"session": session,
         "assessment_attendances": scope_records(Attendance.objects.filter(session=session, status="completed"), request.user, "record_credentials").select_related("person"),
         "can_manage": session_queryset(request.user, "manage_sessions").filter(pk=session.pk).exists(),
         "can_import": session_queryset(request.user, "import_attendance").filter(pk=session.pk).exists()},
    )



@login_required
@require_capability("manage_sessions")
def session_close(request, session_id):
    """Close a session - mark it closed, mark partials as absent."""
    session = get_object_or_404(
        _sessions_visible_to(request.user, "manage_sessions").select_related("programme", "host_unit"), id=session_id
    )

    # Count attendance states for the confirmation page
    attendances = scope_records(Attendance.objects.all(), request.user, "view_sessions").filter(session=session)
    total_checked_in = attendances.count()
    completed = attendances.filter(status="completed").count()
    partial = attendances.filter(status="partial").count()

    if request.method == "POST":
        session.status = "closed"
        session.save(update_fields=["status"])

        messages.success(request, f"Session '{session.title}' closed.")
        return redirect("session_list")

    return render(
        request,
        "sessions/session_close.html",
        {
            "session": session,
            "total_checked_in": total_checked_in,
            "completed": completed,
            "partial": partial,
        },
    )

# ---------------------------------------------------------------------------
# HTMX fragment endpoints (used by the session console)
# ---------------------------------------------------------------------------


@login_required
@require_capability("manage_sessions")
def session_qr_fragment(request, session_id):
    """HTML fragment: QR code + countdown. Polled by HTMX every 30s."""
    import base64
    import io

    import qrcode

    session = get_object_or_404(_sessions_visible_to(request.user, "manage_sessions"), id=session_id)
    token = _rotating_token(session)

    # Build the FULL URL including the token
    checkin_url = request.build_absolute_uri(
        f"/checkin/{session.session_code}/?t={token}"
    )

    # Generate the QR code on the server
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(checkin_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to base64 so we can embed it directly in the HTML
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode()

    expires_in = 30 - (int(time.time()) % 30)

    return render(
        request,
        "sessions/partials/_qr_fragment.html",
        {
            "session": session,
            "qr_image_data": f"data:image/png;base64,{img_str}",
            "checkin_url": checkin_url,
            "expires_in": expires_in,
        },
    )

@login_required
@require_capability("view_sessions")
def session_roster_fragment(request, session_id):
    """HTML fragment: attendance count + list. Polled by HTMX every 5s."""
    session = get_object_or_404(_sessions_visible_to(request.user), id=session_id)

    attendances = (
        scope_records(Attendance.objects.all(), request.user, "view_sessions").filter(session=session)
        .select_related("person")
        .order_by("-check_in_at")
    )

    confirm_ids = set(scope_records(Attendance.objects.all(), request.user, "edit_records").filter(
        session=session, status="partial", check_in_at__isnull=False,
    ).values_list("pk", flat=True)) if session.status != "cancelled" else set()
    for attendance in attendances:
        attendance.can_confirm = attendance.pk in confirm_ids
    return render(
        request,
        "sessions/partials/_roster_fragment.html",
        {
            "session": session,
            "attendances": attendances,
            "count": attendances.count(),
        },
    )


# ---------------------------------------------------------------------------
# JSON endpoints (available for future mobile / external clients)
# ---------------------------------------------------------------------------


@login_required
@require_capability("manage_sessions")
def session_qr_json(request, session_id):
    """JSON endpoint that returns the currently-valid rotating token."""
    session = get_object_or_404(_sessions_visible_to(request.user, "manage_sessions"), id=session_id)
    token = _rotating_token(session)
    checkin_url = request.build_absolute_uri(
        f"/checkin/{session.session_code}/?t={token}"
    )
    return JsonResponse(
        {
            "token": token,
            "checkin_url": checkin_url,
            "bucket": int(time.time()) // 30,
            "expires_in": 30 - (int(time.time()) % 30),
        }
    )


@login_required
@require_capability("view_sessions")
def session_live_stats(request, session_id):
    """JSON endpoint for external clients that want live stats."""
    session = get_object_or_404(_sessions_visible_to(request.user), id=session_id)

    count = scope_records(Attendance.objects.all(), request.user, "view_sessions").filter(session=session).count()

    recent = list(
        scope_records(Attendance.objects.all(), request.user, "view_sessions").filter(session=session)
        .select_related("person")
        .order_by("-check_in_at")[:5]
        .values("person__full_name", "check_in_at")
    )

    return JsonResponse({"count": count, "recent": recent})


# ---------------------------------------------------------------------------
# 90-day follow-up survey (public)
# ---------------------------------------------------------------------------


def followup_form(request, token):
    """Public 90-day follow-up survey."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    pt = (
        PassportToken.objects.filter(
            token_hash=token_hash, revoked_at__isnull=True
        )
        .select_related("person")
        .first()
    )

    if not pt:
        return render(request, "public/followup_invalid.html")

    person = pt.person
    attendance = (
        Attendance.objects.filter(person=person, status="completed")
        .order_by("-check_out_at")
        .first()
    )

    if request.method == "POST":
        applied = request.POST.get("applied", "").strip()
        confidence = request.POST.get("confidence", "")
        template, _ = SurveyTemplate.objects.get_or_create(
            name="90-Day Follow-Up",
            defaults={"type": "follow_up", "definition": {}},
        )

        if attendance:
            SurveyResponse.objects.update_or_create(
                attendance=attendance,
                type="follow_up",
                defaults={
                    "template": template,
                    "csat": int(confidence) if confidence.isdigit() else None,
                    "answers": {"applied": applied},
                },
            )

        # Revoke the token so it can't be reused
        pt.revoked_at = timezone.now()
        pt.save()

        return render(request, "public/followup_done.html")

    return render(
        request,
        "public/followup.html",
        {"person": person, "attendance": attendance},
    )
    
    
@login_required
@require_capability("manage_sessions")
def session_checkout_console(request, session_id):
    """Display the check-out QR code for a session."""
    session = get_object_or_404(_sessions_visible_to(request.user, "manage_sessions"), id=session_id)

    checkout_url = request.build_absolute_uri(
        reverse("checkout_identify", kwargs={"code": session.session_code})
    )
    
    qr_svg_uri = qr_data_uri(checkout_url)
    
    return render(
        request,
        "sessions/session_checkout_console.html",
        {
            "session": session,
            "checkout_url": checkout_url,
            "qr_image_data": qr_svg_uri,
        },
    )


def checkout_identify(request, code):
    """Participant scans the checkout QR and enters their email/ID."""
    session = get_object_or_404(
        Session, session_code=code, status__in=["scheduled", "open"]
    )

    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        try:
            submitted = identity_data(
                email=identifier if "@" in identifier else "",
                staff_or_student_no=identifier if "@" not in identifier else "",
                full_name=request.POST.get("full_name", ""),
            )
            result = match_identity(submitted, source="checkout")
        except ValidationError:
            result = None
        person = None
        if result and result.status == "matched":
            person = result.person if result.person.attendances.filter(session=session).exists() else None
        elif result and result.status == "conflict":
            eligible = list(Person.objects.filter(pk__in=result.candidate_ids, attendances__session=session, is_anonymised=False, merged_into__isnull=True).distinct()[:2])
            if len(eligible) == 1:
                person = eligible[0]
            else:
                messages.error(request, "More than one participant uses that identifier. Add your full name or ask the facilitator for help.")
        if person:
            return redirect("checkout", code=session.session_code, person_id=person.pk)
        elif not result or result.status != "conflict":
            messages.error(request, "We could not match a checked-in participant with those details.")

    return render(request, "public/checkout_identify.html", {"session": session})
