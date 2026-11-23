"""AUTH-03: real authenticator verification at every privileged boundary."""

import hashlib
import secrets
from base64 import b32encode
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, override_settings
from django.urls import path, reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from freezegun import freeze_time

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.tests.helpers import force_login_with_mfa
from apps.accounts.views import totp_setup
from apps.core.models import AcademicPeriod
from apps.registry.models import PassportToken, Person


@pytest.fixture
def admin_user(db):
    user = get_user_model().objects.create_user(
        username="mfa-admin", email="mfa-admin@knust.edu.gh", is_staff=True
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    return user


def _code(device):
    return str(
        totp(
            device.bin_key,
            step=device.step,
            t0=device.t0,
            digits=device.digits,
            drift=device.drift,
        )
    ).zfill(device.digits)


def _assert_mfa_redirect(response, destination):
    assert response.status_code == 302
    assert response.url == f"{reverse('totp_setup')}?{urlencode({'next': destination})}"


@pytest.mark.parametrize(
    "destination",
    [
        "/hub/", "/sessions/", "/dashboard/", "/library/", "/calendar/",
        "/memos/", "/nominations/", "/import/upload/", "/credentials/review/",
        "/admin/", "/admin-tools/audit-log/", "/people/", "/scout/",
        "/recognition/", "/partners/", "/my-passport/",
    ],
)
@pytest.mark.parametrize("enrolled", [False, True])
def test_privileged_routes_require_session_verification(
    client, admin_user, destination, enrolled
):
    if enrolled:
        TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
        UserAccount.objects.filter(user=admin_user).update(mfa_enabled=True)
    client.force_login(admin_user)

    _assert_mfa_redirect(client.get(destination), destination)


def test_privileged_post_is_blocked_before_the_write(client, admin_user):
    client.force_login(admin_user)
    destination = reverse("calendar_create")
    response = client.post(
        destination,
        {
            "year_start": 2026, "year_end": 2027, "semester": 1,
            "start_date": "2026-09-01", "end_date": "2027-01-31",
        },
    )
    _assert_mfa_redirect(response, destination)
    assert not AcademicPeriod.objects.exists()


def test_query_string_is_preserved_for_verification(client, admin_user):
    client.force_login(admin_user)
    destination = "/sessions/?status=open"
    _assert_mfa_redirect(client.get(destination), destination)


def test_new_privileged_routes_are_protected_by_default(client, admin_user):
    calls = []

    def new_tool(request):
        calls.append(request)
        return HttpResponse("privileged data")

    routes = (
        path("new-staff-tool/", new_tool, name="new_staff_tool"),
        path("account/totp/setup/", totp_setup, name="totp_setup"),
    )
    client.force_login(admin_user)
    with override_settings(ROOT_URLCONF=routes):
        _assert_mfa_redirect(client.get("/new-staff-tool/"), "/new-staff-tool/")
    assert not calls


def test_django_superuser_without_hub_account_requires_mfa(client, db):
    user = get_user_model().objects.create_superuser(
        username="django-root", email="root@knust.edu.gh", password="test-password"
    )
    client.force_login(user)
    _assert_mfa_redirect(client.get("/admin/"), "/admin/")


@pytest.mark.parametrize("destination", ["/", "/health/", "/resend/", "/sas/"])
def test_public_flows_remain_accessible_without_admin_mfa(
    client, admin_user, destination
):
    client.force_login(admin_user)
    assert client.get(destination).status_code == 200


def test_personal_passport_is_unaffected_by_admin_mfa(client, admin_user):
    person = Person.objects.create(
        full_name="Passport Owner", affiliation="staff", title="Dr.",
        category="academic", unit_other="KEC", email=admin_user.email,
    )
    account = admin_user.hub_account
    account.person = person
    account.save(update_fields=["person"])
    raw = secrets.token_urlsafe(32)
    PassportToken.objects.create(
        person=person, token_hash=hashlib.sha256(raw.encode()).hexdigest()
    )
    client.force_login(admin_user)
    response = client.get(reverse("passport", args=[raw]))
    assert response.status_code == 200
    assert DEVICE_ID_SESSION_KEY not in client.session


def test_non_admin_hub_user_does_not_require_authenticator(client, admin_user):
    admin_user.hub_account.roles.all().delete()
    client.force_login(admin_user)
    assert client.get(reverse("hub_home")).status_code == 200
    assert not TOTPDevice.objects.filter(user=admin_user).exists()


def test_authentication_and_signout_remain_reachable(client, admin_user):
    client.force_login(admin_user)
    assert client.get(reverse("account_logout")).status_code == 200
    response = client.get("/admin/login/")
    assert response.status_code == 302
    assert response.url == "/admin/"


def test_enrollment_shows_base32_key_and_reuses_pending_device(client, admin_user):
    client.force_login(admin_user)
    response = client.get(reverse("totp_setup"))
    device = TOTPDevice.objects.get(user=admin_user)
    assert response.status_code == 200
    assert response.context["is_enrolled"] is False
    assert b32encode(device.bin_key) in response.content
    assert "no-store" in response["Cache-Control"]

    qr = client.get(reverse("totp_setup_qr"))
    assert qr.status_code == 200
    assert qr["Content-Type"] == "image/png"
    assert qr.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "no-store" in qr["Cache-Control"]
    assert TOTPDevice.objects.filter(user=admin_user).count() == 1
    device.refresh_from_db()
    assert device.confirmed is False


def test_enrolled_challenge_never_reveals_secret_or_enrollment_qr(client, admin_user):
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    client.force_login(admin_user)
    response = client.get(reverse("totp_setup"))
    assert response.status_code == 200
    assert response.context["is_enrolled"] is True
    assert device.key.encode() not in response.content
    assert b32encode(device.bin_key) not in response.content
    assert reverse("totp_setup_qr").encode() not in response.content
    assert b"Verify your sign-in" in response.content
    assert client.get(reverse("totp_setup_qr")).status_code == 403


@freeze_time("2026-11-17 10:00:00")
@pytest.mark.parametrize("enrolled", [False, True])
def test_real_code_enrolls_or_verifies_then_returns_to_original_page(
    client, admin_user, enrolled
):
    device = TOTPDevice.objects.create(
        user=admin_user, name="default", confirmed=enrolled
    )
    client.force_login(admin_user)
    destination = "/sessions/?status=open"
    response = client.post(
        reverse("totp_setup"), {"token": _code(device), "next": destination}
    )
    assert response.status_code == 302
    assert response.url == destination
    device.refresh_from_db()
    assert device.confirmed is True
    assert client.session[DEVICE_ID_SESSION_KEY] == device.persistent_id
    account = UserAccount.objects.get(user=admin_user)
    assert account.mfa_enabled is True
    assert client.get(destination).status_code == 200
    assert client.get("/admin/").status_code == 200


@pytest.mark.parametrize(
    "destination",
    [
        "https://outside.example/", "//outside.example/", "not-a-url",
        "javascript:alert(1)", "/account/totp/setup", "/account/totp/setup/",
        "/account/totp/setup/qr/",
    ],
)
@freeze_time("2026-11-17 10:00:00")
def test_verification_rejects_external_and_looping_redirects(
    client, admin_user, destination
):
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    client.force_login(admin_user)
    response = client.post(
        reverse("totp_setup"), {"token": _code(device), "next": destination}
    )
    assert response.status_code == 302
    assert response.url == reverse("hub_home")


def test_invalid_code_does_not_enrol_or_verify_and_is_throttled(client, admin_user):
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=False)
    client.force_login(admin_user)
    response = client.post(reverse("totp_setup"), {"token": "invalid"})
    assert response.status_code == 200
    assert b"Invalid code" in response.content
    assert DEVICE_ID_SESSION_KEY not in client.session
    device.refresh_from_db()
    assert device.confirmed is False
    assert device.throttling_failure_count == 1
    _assert_mfa_redirect(client.get("/hub/"), "/hub/")


@freeze_time("2026-11-17 10:00:00")
def test_used_code_cannot_verify_a_second_session(client, admin_user):
    device = TOTPDevice.objects.create(user=admin_user, name="default", confirmed=True)
    token = _code(device)
    client.force_login(admin_user)
    assert client.post(reverse("totp_setup"), {"token": token}).status_code == 302

    second_client = Client()
    second_client.force_login(admin_user)
    response = second_client.post(reverse("totp_setup"), {"token": token})
    assert response.status_code == 200
    assert DEVICE_ID_SESSION_KEY not in second_client.session
    _assert_mfa_redirect(second_client.get("/hub/"), "/hub/")


@pytest.mark.parametrize("device_state", ["unconfirmed", "foreign", "deleted"])
def test_invalid_session_device_cannot_satisfy_mfa(client, admin_user, device_state):
    owner = admin_user
    if device_state == "foreign":
        owner = get_user_model().objects.create_user(username="other-device-owner")
    device = TOTPDevice.objects.create(
        user=owner, name="default", confirmed=device_state != "unconfirmed"
    )
    device_id = device.persistent_id
    if device_state == "deleted":
        device.delete()
    client.force_login(admin_user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device_id
    session.save()
    _assert_mfa_redirect(client.get("/hub/"), "/hub/")
    assert DEVICE_ID_SESSION_KEY not in client.session


def test_logout_requires_verification_on_next_login(client, admin_user):
    force_login_with_mfa(client, admin_user)
    assert client.get("/hub/").status_code == 200
    client.logout()
    client.force_login(admin_user)
    _assert_mfa_redirect(client.get("/hub/"), "/hub/")


def test_idle_logout_clears_authenticator_session(client, admin_user):
    force_login_with_mfa(client, admin_user)
    session = client.session
    session["last_activity"] = (timezone.now() - timedelta(minutes=31)).isoformat()
    session.save()
    response = client.get("/hub/")
    assert response.status_code == 302
    assert response.url == reverse("login")
    assert DEVICE_ID_SESSION_KEY not in client.session


def test_anonymous_enrollment_and_qr_do_not_expose_secrets(client, db):
    response = client.get(reverse("totp_setup"))
    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))
    assert client.get(reverse("totp_setup_qr")).status_code == 403
    assert not TOTPDevice.objects.exists()
