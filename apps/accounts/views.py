""" the partern for the hub  view """

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.accounts.permissions import scope_queryset
from apps.registry.models import Person
from django.shortcuts import render
from apps.accounts.permissions import get_user_scopes, is_super_admin
import io
import qrcode



@login_required
def person_list(request):
    qs = Person.objects.all().order_by("full_name")
    qs = scope_queryset(qs, request.user, {"unit": "unit_id"})
    return render(
        request,
        "accounts/person_list.html",
        {"people": qs, "count": qs.count()},
    )
    
    
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

    person = None
    try:
        person = request.user.hub_account.person
    except Exception:
        pass

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
        },
    )
    
    
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_otp import login as otp_login
from django.shortcuts import render, redirect
from django.contrib import messages





def totp_setup(request):
    """Setup page for TOTP two-factor authentication.

    Forces Super Admins to enroll in an authenticator app before accessing the Hub.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from django_otp import login as otp_login
    from django.contrib import messages
    from django.shortcuts import render, redirect

    if not request.user.is_authenticated:
        return redirect("/login/")

    device, _ = TOTPDevice.objects.get_or_create(
        user=request.user,
        name="default",
        defaults={"confirmed": False},
    )

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if device.verify_token(token):
            device.confirmed = True
            device.save()
            otp_login(request, device)
            messages.success(request, "Two-factor authentication enabled.")
            return redirect("/hub/")
        else:
            messages.error(
                request,
                "Invalid code. Check your phone's clock and try again.",
            )

    return render(
        request,
        "accounts/totp_setup.html",
        {"device": device},
    )


def totp_setup_qr(request):
    """Serve the QR code image for the current user's TOTP device."""
    import io
    import qrcode
    from django.http import HttpResponse
    from django_otp.plugins.otp_totp.models import TOTPDevice

    if not request.user.is_authenticated:
        return HttpResponse(status=403)

    device, _ = TOTPDevice.objects.get_or_create(
        user=request.user,
        name="default",
        defaults={"confirmed": False},
    )

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
    except Exception:
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