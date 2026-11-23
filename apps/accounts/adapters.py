"""KNUST-specific allauth social-login checks."""

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.http import HttpResponseForbidden


class KnustSocialAccountAdapter(DefaultSocialAccountAdapter):
    """Require a verified KNUST email and a KNUST hosted-domain claim."""

    def pre_social_login(self, request, sociallogin):
        if sociallogin.account.provider != "google":
            return

        allowed_domains = {
            domain.casefold().strip()
            for domain in getattr(
                settings,
                "KNUST_ALLOWED_EMAIL_DOMAINS",
                ("knust.edu.gh", "st.knust.edu.gh"),
            )
        }
        extra_data = sociallogin.account.extra_data or {}
        email = str(extra_data.get("email", "")).strip().casefold()
        email_domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        hosted_domain = str(extra_data.get("hd", "")).strip().casefold()
        email_verified = any(
            address.email.casefold() == email and address.verified
            for address in sociallogin.email_addresses
        )

        if (
            email_domain not in allowed_domains
            or hosted_domain not in allowed_domains
            or not email_verified
        ):
            raise ImmediateHttpResponse(
                HttpResponseForbidden(
                    "Sign-in requires a verified KNUST Google Workspace account."
                )
            )
