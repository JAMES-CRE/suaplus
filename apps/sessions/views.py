"""Public check-in and check-out views."""

import hashlib
import hmac
import secrets
import time

from django.conf import settings
from django.db.models import Q
from apps.sessions.qrhelper import qr_data_uri
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import get_user_scopes, is_super_admin
from apps.core.rate_limit import rate_limit
from apps.library.models import Level, SurveyTemplate
from apps.notifications.services import send_notification
from apps.passport.models import Evidence
from apps.registry.models import Consent, Organisation, Person, PassportToken
from apps.sessions.forms import CheckinForm, CheckoutForm, SessionForm
from apps.sessions.models import (
    Attendance,
    Facilitation,
    Session,
    SurveyResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _match_person(affiliation, email, phone, staff_no, full_name, age_band="unknown"):
    """Find an existing Person or create one. Returns the Person."""
    qs = Person.objects.all()

    if staff_no:
        found = qs.filter(staff_or_student_no=staff_no).first()
        if found:
            return found

    if email:
        found = qs.filter(email__iexact=email).first()
        if found:
            return found

    if phone:
        found = qs.filter(phone=phone).first()
        if found:
            return found

    return Person.objects.create(
        affiliation=affiliation,
        staff_or_student_no=staff_no or None,
        email=email or None,
        phone=phone or None,
        full_name=full_name,
        age_band=age_band,
    )


def _create_attendance_evidence(person, session):
    """Write level-1 evidence for each competency in the session."""
    attendance_level = Level.objects.filter(
        framework__is_knust=True, ordinal=1
    ).first()
    if not attendance_level:
        return
    for sc in session.competencies.select_related("competency"):
        Evidence.objects.create(
            person=person,
            competency=sc.competency,
            level=attendance_level,
            type="attendance",
            source_type="attendance",
            framework_version="v1",
        )



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


@rate_limit("checkin", max_requests=20, window_seconds=3600)
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

            org = None
            if data["affiliation"] == "external" and data["consent"]:
                org_name = data.get("organisation_name", "").strip()
                if org_name:
                    org, _ = Organisation.objects.get_or_create(
                        name=org_name, defaults={"type": "other"}
                    )

            age_band = data.get("age_band", "unknown")
            phone_value = (
                "" if age_band == "under_18" else data.get("phone", "").strip()
            )

            with transaction.atomic():
                person = _match_person(
                    affiliation=data["affiliation"],
                    email=data.get("email", "").strip(),
                    phone=phone_value,
                    staff_no=data.get("staff_or_student_no", "").strip(),
                    full_name=data["full_name"].strip(),
                    age_band=age_band,
                )

                if data["affiliation"] == "external" and org is not None:
                    person.organisation = org
                    person.save(update_fields=["organisation"])

                Consent.objects.create(
                    person=person,
                    type="data_processing",
                    given_by=person.full_name,
                    notice_version="1.0",
                )

                if age_band == "under_18":
                    Consent.objects.create(
                        person=person,
                        type="guardian",
                        given_by=data.get("guardian_name", "").strip(),
                        notice_version="1.0",
                    )

                if data.get("sms_consent") and person.phone:
                    Consent.objects.create(
                        person=person,
                        type="sms",
                        given_by=person.full_name,
                        notice_version="1.0",
                    )

                attendance, created = Attendance.objects.get_or_create(
                    session=session,
                    person=person,
                    defaults={
                        "method": "code",
                        "check_in_at": timezone.now(),
                        "status": "partial",
                    },
                )

                if not created:
                    messages.info(
                        request, "You were already checked in to this session."
                    )
                else:
                    _create_attendance_evidence(person, session)

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
    """Check-out + exit survey."""
    session = get_object_or_404(Session, session_code=code)
    attendance = get_object_or_404(
        Attendance, session=session, person_id=person_id
    )

    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                attendance.check_out_at = timezone.now()
                attendance.status = "completed"
                attendance.save(update_fields=["check_out_at", "status"])

                template = SurveyTemplate.objects.filter(
                    type="exit", is_default=True
                ).first()
                if template:
                    SurveyResponse.objects.create(
                        attendance=attendance,
                        template=template,
                        type="exit",
                        csat=form.cleaned_data["csat"],
                        answers={
                            "what_will_you_apply": form.cleaned_data[
                                "what_will_you_apply"
                            ],
                        },
                        interested_in_training=form.cleaned_data[
                            "interested_in_training"
                        ],
                    )

                raw_token = secrets.token_urlsafe(32)
                PassportToken.objects.create(
                    person=attendance.person,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                )
                passport_url = f"{settings.SITE_URL}/p/{raw_token}/"
                send_notification(
                    person=attendance.person,
                    template_key="passport_link",
                    context={
                        "name": attendance.person.full_name,
                        "session_title": session.title,
                        "passport_url": passport_url,
                    },
                )
            return render(
                request,
                "public/checkout_done.html",
                {"session": session},
            )
    else:
        form = CheckoutForm()

    return render(
        request,
        "public/checkout.html",
        {"session": session, "attendance": attendance, "form": form},
    )


# ---------------------------------------------------------------------------
# Session management (Hub)
# ---------------------------------------------------------------------------


@login_required
def session_list(request):
    """List of sessions the user can see."""
    from apps.programmes.models import Programme

    qs = Session.objects.select_related("programme", "host_unit").annotate(
        attendance_count=Count("attendances")
    )

    # Scoping based on role
    if not is_super_admin(request.user):
        try:
            account = request.user.hub_account
            # Facilitators only see sessions where they are listed as a facilitator
            qs = qs.filter(facilitators__person=account.person).distinct()
        except AttributeError:
            # User has no hub_account (e.g. not properly provisioned)
            qs = qs.none()

    # Optional filters
    programme_id = request.GET.get("programme")
    status = request.GET.get("status")
    if programme_id:
        qs = qs.filter(programme_id=programme_id)
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-start_at")

    # Programme filter choices
    programmes = Programme.objects.filter(status="active").order_by("name")

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
        },
    )

@login_required
def session_create(request):
    """Create a new session."""
    from datetime import datetime

    if request.method == "POST":
        form = SessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)

            start_date = form.cleaned_data["start_date"]
            start_time = form.cleaned_data["start_time"]
            end_date = form.cleaned_data["end_date"]
            end_time = form.cleaned_data["end_time"]

            session.start_at = datetime.combine(start_date, start_time)
            session.end_at = datetime.combine(end_date, end_time)

            session.save()

            # Link creator as lead facilitator
            try:
                account = request.user.hub_account
                if account.person:
                    Facilitation.objects.get_or_create(
                        session=session,
                        person=account.person,
                        defaults={"role": "lead"},
                    )
            except AttributeError:
                pass

            messages.success(
                request,
                f"Session '{session.title}' created. "
                f"Session code: {session.session_code}",
            )
            return redirect("session_console", session_id=session.id)
    else:
        form = SessionForm()

    return render(
        request,
        "sessions/session_create.html",
        {"form": form},
    )


@login_required
def session_console(request, session_id):
    """Live console for a session: rotating QR, roster, close button."""
    session = get_object_or_404(
        Session.objects.select_related("programme"), id=session_id
    )

    return render(
        request,
        "sessions/session_console.html",
        {"session": session},
    )



@login_required
def session_close(request, session_id):
    """Close a session - mark it closed, mark partials as absent."""
    session = get_object_or_404(
        Session.objects.select_related("programme", "host_unit"), id=session_id
    )

    # Count attendance states for the confirmation page
    attendances = Attendance.objects.filter(session=session)
    total_checked_in = attendances.count()
    completed = attendances.filter(status="completed").count()
    partial = attendances.filter(status="partial").count()

    if request.method == "POST":
        session.status = "closed"
        session.save(update_fields=["status"])

        Attendance.objects.filter(session=session, status="partial").update(
            status="absent"
        )

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
def session_qr_fragment(request, session_id):
    """HTML fragment: QR code + countdown. Polled by HTMX every 30s."""
    import io
    import base64
    import qrcode
    from urllib.parse import quote

    session = get_object_or_404(Session, id=session_id)
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
def session_roster_fragment(request, session_id):
    """HTML fragment: attendance count + list. Polled by HTMX every 5s."""
    session = get_object_or_404(Session, id=session_id)

    attendances = (
        Attendance.objects.filter(session=session)
        .select_related("person")
        .order_by("-check_in_at")
    )

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
def session_qr_json(request, session_id):
    """JSON endpoint that returns the currently-valid rotating token."""
    session = get_object_or_404(Session, id=session_id)
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
def session_live_stats(request, session_id):
    """JSON endpoint for external clients that want live stats."""
    session = get_object_or_404(Session, id=session_id)

    count = Attendance.objects.filter(session=session).count()

    recent = list(
        Attendance.objects.filter(session=session)
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
def session_checkout_console(request, session_id):
    """Display the check-out QR code for a session."""
    session = get_object_or_404(Session, id=session_id)

    checkout_url = request.build_absolute_uri(
        f"/checkout/identify/{session.session_code}/"
    )

    return render(
        request,
        "sessions/session_checkout_console.html",
        {"session": session, "checkout_url": checkout_url},
    )


def checkout_identify(request, code):
    """Participant scans the checkout QR and enters their email/ID."""
    session = get_object_or_404(
        Session, session_code=code, status__in=["scheduled", "open"]
    )

    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()

        person = Person.objects.filter(
            Q(email__iexact=identifier) | Q(staff_or_student_no__iexact=identifier)
        ).first()

        if person:
            attendance = Attendance.objects.filter(session=session, person=person).first()
            if attendance:
                return redirect(
                    "checkout",
                    code=session.session_code,
                    person_id=person.id,
                )
            else:
                messages.error(request, "You were not checked in to this session.")
        else:
            messages.error(request, "We couldn't find a record with that email or ID.")

    return render(request, "public/checkout_identify.html", 
                  {"session": session})  
    
    
    
@login_required
def session_checkout_console(request, session_id):
    """Display the check-out QR code for a session."""
    session = get_object_or_404(Session, id=session_id)
    
    # Build the URL that the QR code will point to
    checkout_url = request.build_absolute_uri(
        f"/checkout/identify/{session.session_code}/"
    )
    
    # Generate the SVG QR code using your helper
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

        # Find the person by email or staff number
        person = Person.objects.filter(
            Q(email__iexact=identifier) | Q(staff_or_student_no__iexact=identifier)
        ).first()

        if person:
            # Check if they actually attended this session
            attendance = Attendance.objects.filter(session=session, person=person).first()
            if attendance:
                return redirect(
                    "checkout",
                    code=session.session_code,
                    person_id=person.id,
                )
            else:
                messages.error(request, "You were not checked in to this session.")
        else:
            messages.error(request, "We couldn't find a record with that email or ID.")

    return render(request, "public/checkout_identify.html", {"session": session})
    