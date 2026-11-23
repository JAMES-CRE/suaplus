from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

from apps.accounts.mfa import has_verified_totp, requires_mfa


class SuperAdminMFAMiddleware(MiddlewareMixin):
    """Require session MFA on every privileged route, including Django admin.

    Public participant flows and authentication remain reachable. New routes
    are protected by default instead of requiring another protected-path entry.
    """

    public_view_names = frozenset(
        {
            "home",
            "health_check",
            "liveness",
            "readiness",
            "login",
            "cancel_login_code",
            "profile_completion",
            "totp_setup",
            "totp_setup_qr",
            "checkin",
            "checkin_done",
            "checkout",
            "checkout_identify",
            "passport",
            "passport_profile",
            "passport_transcript",
            "passport_transcript_pdf",
            "report_error",
            "resend_link",
            "sas_start",
            "sas_confirm",
            "followup_form",
            "badge_verify",
            "badge_public_key",
        }
    )

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if request.path_info.startswith("/accounts/"):
            return None
        if match and (
            (not match.namespace and match.url_name in self.public_view_names)
            or (match.namespace == "admin" and match.url_name in {"login", "logout"})
        ):
            return None
        if requires_mfa(request.user) and not has_verified_totp(request.user):
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{reverse('totp_setup')}?{query}")
        return None
