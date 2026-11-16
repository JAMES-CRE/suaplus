"""Secure passport-link issuance shared by checkout, imports, and resend."""

import hashlib
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.registry.models import PassportToken


def issue_passport_link(person):
    """Revoke previous links and issue one fresh, hashed 256-bit token."""
    raw_token = secrets.token_urlsafe(32)
    with transaction.atomic():
        PassportToken.objects.filter(
            person=person, revoked_at__isnull=True
        ).update(revoked_at=timezone.now())
        PassportToken.objects.create(
            person=person,
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        )
    return f"{settings.SITE_URL}/p/{raw_token}/"
