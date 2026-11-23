

import uuid
from datetime import UTC, datetime

import jwt
from django.conf import settings


def expire_due_records(today=None):
    """Mark credentials and evidence expired after their stated expiry date."""
    from django.db import transaction
    from django.utils import timezone as django_timezone

    from apps.passport.models import Credential, Evidence

    today = today or django_timezone.localdate()
    with transaction.atomic():
        credentials = Credential.objects.filter(
            status="valid", expires_at__lt=today
        ).update(status="expired")
        evidence = Evidence.objects.filter(
            status="valid", expires_at__lt=today
        ).update(status="expired", is_expired=True)
    return {"credentials": credentials, "evidence": evidence}

def generate_badge_json(person, competency, level):
    """Generate a signed Open Badge 3.0 JSON-LD credential."""
    
    # 1. Base Open Badges 3.0 Structure
    badge_id = f"urn:uuid:{uuid.uuid4()}"
    issued_at = datetime.now(UTC).isoformat()
    
    credential = {
        "@context": [
            "https://www.w3.org/2018/credentials/v1",
            "https://purl.imsglobal.org/spec/ob/v3p0/context-3.0.3.json"
        ],
        "id": badge_id,
        "type": ["VerifiableCredential", "OpenBadgeCredential"],
        "issuer": {
            "id": settings.SITE_URL,
            "type": "Profile",
            "name": "Kwame Nkrumah University of Science and Technology",
            "url": "https://knust.edu.gh"
        },
        "issuanceDate": issued_at,
        "credentialSubject": {
            "id": f"mailto:{person.email}",
            "type": "AchievementSubject",
            "achievement": {
                "id": f"{settings.SITE_URL}/badges/{competency.code}",
                "type": "Achievement",
                "name": f"{competency.code} - {competency.title}",
                "description": competency.description,
                "criteria": {
                    "narrative": f"Reached Level {level.ordinal} ({level.label}) in {competency.title}."
                }
            }
        }
    }

    private_key = getattr(settings, "BADGE_PRIVATE_KEY", "")
    if private_key:
        return jwt.encode({"vc": credential}, private_key, algorithm="RS256", headers={"kid": getattr(settings, "BADGE_KEY_ID", "current")})
    if not settings.DEBUG:
        return None  # Issuance recorded, signing disabled until approved keys are configured.
    return jwt.encode({"vc": credential}, settings.SECRET_KEY, algorithm="HS256")


def verify_badge(encoded_jwt):
    try:
        algorithm = jwt.get_unverified_header(encoded_jwt).get("alg")
        if algorithm == "RS256" and getattr(settings, "BADGE_PUBLIC_KEY", ""):
            decoded = jwt.decode(encoded_jwt, settings.BADGE_PUBLIC_KEY, algorithms=["RS256"])
        elif algorithm == "HS256" and settings.DEBUG:
            decoded = jwt.decode(encoded_jwt, settings.SECRET_KEY, algorithms=["HS256"])
        else:
            return None
        return decoded["vc"]
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
