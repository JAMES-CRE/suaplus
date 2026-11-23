"""Explicit authenticated-session fixtures for tests unrelated to MFA."""

from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.mfa import requires_mfa


def force_login_with_mfa(client, user):
    """Simulate a completed admin login without bypassing the MFA middleware.

    MFA regression tests use plain force_login and submit real authenticator
    codes instead. Other role-based tests retain their normal login semantics.
    """
    client.force_login(user)
    if requires_mfa(user):
        device, _ = TOTPDevice.objects.get_or_create(
            user=user, name="test-authenticator", defaults={"confirmed": True}
        )
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
