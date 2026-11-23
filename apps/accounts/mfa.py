"""Shared policy for privileged authenticator verification."""

from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.permissions import is_super_admin


def requires_mfa(user):
    """Sua+ super admins and Django superusers must verify an authenticator."""
    return user.is_authenticated and (user.is_superuser or is_super_admin(user))


def has_verified_totp(user):
    """Enrollment flags are not proof that this session completed MFA."""
    return (
        user.is_authenticated
        and getattr(user, "is_verified", lambda: False)()
        and isinstance(getattr(user, "otp_device", None), TOTPDevice)
    )
