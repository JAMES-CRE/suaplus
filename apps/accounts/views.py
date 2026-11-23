""" the partern for the hub  view """

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.accounts.permissions import can, person_queryset, require_capability


@login_required
@require_capability("view_people")
def person_list(request):
    from django.core.paginator import Paginator
    from django.db.models import Q

    qs = person_queryset(request.user).select_related("unit", "organisation").order_by("full_name")
    query = request.GET.get("q", "").strip()
    if query:
        qs = qs.filter(Q(full_name__icontains=query) | Q(email__icontains=query) | Q(staff_or_student_no__icontains=query))
    page = Paginator(qs, 50).get_page(request.GET.get("page"))
    return render(request, "accounts/person_list.html", {"people": page, "count": qs.count(), "q": query})
    
    
def cancel_login_code(request):
    for key in list(request.session.keys()):
        if "login_code" in key or "account_login" in key:
            del request.session[key]
    request.session.flush()
    from django.shortcuts import redirect
    return redirect("/login/")

def hub_home(request):
    if not request.user.is_authenticated:
        from django.shortcuts import redirect
        return redirect("/login/")

    # Get the user's roles to decide what to show them
    from apps.accounts.permissions import get_user_scopes, is_super_admin
    scopes = get_user_scopes(request.user)
    roles = {s.role for s in scopes}
    
    is_admin = is_super_admin(request.user)
    is_owner = "programme_owner" in roles or is_admin
    is_facilitator = "facilitator" in roles or is_admin
    is_focal = "focal_person" in roles or is_admin
    is_exec = "executive_viewer" in roles or is_admin
    is_curator = "library_curator" in roles or is_admin

    from django.utils import timezone

    from apps.accounts.context_processors import navigation
    from apps.passport.views import _reviewable_credentials
    from apps.sessions.views import _sessions_visible_to

    nav = navigation(request)["sua_nav"]
    next_session = None
    if nav["sessions"]:
        next_session = (
            _sessions_visible_to(request.user)
            .filter(status__in=["scheduled", "open"], end_at__gte=timezone.now())
            .select_related("programme")
            .order_by("start_at")
            .first()
        )
    pending_reviews = (
        _reviewable_credentials(request.user).count() if nav["reviews"] else 0
    )

    person = None
    try:
        person = request.user.hub_account.person
    except AttributeError:
        person = None

    return render(
        request,
        "hub/home.html",
        {
            "is_admin": is_admin,
            "is_owner": is_owner,
            "is_facilitator": is_facilitator,
            "is_focal": is_focal,
            "is_exec": is_exec,
            "is_curator": is_curator,
            "person": person,
            "can_sessions": nav["sessions"],
            "can_export": can(request.user, "export_reports"),
            "can_nominate": can(request.user, "nominate_trainers"),
            "next_session": next_session,
            "pending_reviews": pending_reviews,
        },
    )
    
    
from base64 import b32encode
from urllib.parse import urlsplit

from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django_otp import login as otp_login
from django_otp import verify_token
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.mfa import has_verified_totp
from apps.accounts.models import UserAccount


def _totp_device_for_user(user):
    """Reuse the enrolled authenticator; never offer a replacement at login."""
    device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
    if device is None:
        device, _ = TOTPDevice.objects.get_or_create(
            user=user, name="default", defaults={"confirmed": False}
        )
    return device


def _totp_next_url(request):
    """Keep the original destination without allowing an external redirect."""
    destination = request.POST.get("next", request.GET.get("next", ""))
    if url_has_allowed_host_and_scheme(
        destination,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        destination_path = urlsplit(destination).path
        if destination_path.startswith("/") and destination_path.rstrip("/") not in {
            reverse("totp_setup").rstrip("/"), reverse("totp_setup_qr").rstrip("/")
        }:
            return destination
    return reverse("hub_home")


@never_cache
@login_required
def totp_setup(request):
    """Enroll once, then require the existing authenticator on each new login."""
    destination = _totp_next_url(request)
    if has_verified_totp(request.user):
        return redirect(destination)

    device = _totp_device_for_user(request.user)
    is_enrolled = device.confirmed

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        with transaction.atomic():
            verified_device = verify_token(request.user, device.persistent_id, token)
            if verified_device is not None:
                if not verified_device.confirmed:
                    verified_device.confirmed = True
                    verified_device.save(update_fields=["confirmed"])
                UserAccount.objects.filter(user=request.user).update(mfa_enabled=True)
                request.session.cycle_key()
                otp_login(request, verified_device)
        if verified_device is not None:
            messages.success(
                request,
                "Two-factor authentication verified."
                if is_enrolled else "Two-factor authentication enabled.",
            )
            return redirect(destination)
        messages.error(request, "Invalid code. Check your authenticator and try again.")

    return render(
        request,
        "accounts/totp_setup.html",
        {
            "is_enrolled": is_enrolled,
            "manual_key": b32encode(device.bin_key).decode() if not is_enrolled else "",
            "next": destination,
        },
    )


@never_cache
@require_GET
def totp_setup_qr(request):
    """Expose the enrollment QR only before an authenticator is confirmed."""
    import io

    import qrcode
    from django.http import HttpResponse

    if not request.user.is_authenticated:
        return HttpResponse(status=403)
    if TOTPDevice.objects.filter(user=request.user, confirmed=True).exists():
        return HttpResponse(status=403)

    device = _totp_device_for_user(request.user)
    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(device.config_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, "PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")



from apps.registry.models import OrgUnit


def profile_completion(request):
    if not request.user.is_authenticated:
        return redirect("/accounts/login/")
    
    try:
        person = request.user.hub_account.person
    except AttributeError:
        return redirect("/hub/")

    errors = []

    if request.method == "POST":
        person.full_name = request.POST.get("full_name", person.full_name).strip()
        person.title = request.POST.get("title", "").strip()
        person.category = request.POST.get("category", "").strip()
        person.phone = request.POST.get("phone", "").strip()

        selected_dept = request.POST.get("department", "")
        
        # Validation: Make sure they selected a department
        if not selected_dept:
            errors.append("Please select a Department or Unit.")
        
        if selected_dept == "other":
            unit_other = request.POST.get("unit_other", "").strip()
            if not unit_other:
                errors.append("Please specify your department when selecting 'Other'.")
            else:
                person.unit_id = None
                person.unit_other = unit_other
        elif selected_dept:
            person.unit_id = int(selected_dept)
            person.unit_other = ""

        if not errors:
            person.save()
            return redirect("/hub/")

    # Fetch Colleges and Departments for the form
    colleges = OrgUnit.objects.filter(active=True, type="college").order_by("name")
    departments = OrgUnit.objects.filter(
        active=True, 
        type__in=["department", "faculty", "school"]
    ).select_related("parent").order_by("name")

    current_college_id = ""
    if person.unit and person.unit.parent:
        current_college_id = person.unit.parent.id

    return render(
        request,
        "accounts/profile_completion.html",
        {
            "person": person,
            "colleges": colleges,
            "departments": departments,
            "current_college_id": current_college_id,
            "errors": errors,
        },
    )
