"""Open Badges 3.0 generator."""

import json
import uuid
from datetime import datetime, timezone
from django.conf import settings
import jwt

def generate_badge_json(person, competency, level):
    """Generate a signed Open Badge 3.0 JSON-LD credential."""
    
    # 1. Base Open Badges 3.0 Structure
    badge_id = f"urn:uuid:{uuid.uuid4()}"
    issued_at = datetime.now(timezone.utc).isoformat()
    
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

    # 2. Sign the badge (JWT)
    # In production, load a real private key from settings/env
    secret_key = settings.SECRET_KEY 
    
    encoded_jwt = jwt.encode(
        {"vc": credential},
        secret_key,
        algorithm="HS256"
    )
    
    return encoded_jwt

def verify_badge(encoded_jwt):
    """Verify a badge's signature."""
    try:
        decoded = jwt.decode(
            encoded_jwt, 
            settings.SECRET_KEY, 
            algorithms=["HS256"]
        )
        return decoded["vc"]
    except jwt.PyJWTError:
        return None