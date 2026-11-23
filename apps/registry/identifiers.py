"""Pure identity normalization shared by forms, models, and matching."""
import re
import unicodedata

from django.core.exceptions import ValidationError
from django.core.validators import validate_email


def normalized_text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def normalize_name(value):
    return normalized_text(value).casefold()


def normalize_number(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).upper()


def normalize_email(value):
    email = normalized_text(value).casefold()
    if email:
        validate_email(email)
    return email


def normalize_phone(value):
    value = normalized_text(value)
    if not value:
        return ""
    if not re.fullmatch(r"[+0-9 ()\-.]+", value):
        raise ValidationError("Use a Ghana local phone number or an international number beginning with +.")
    compact = re.sub(r"[ ()\-.]", "", value)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if compact.startswith("+"):
        digits = compact[1:]
        if not digits.isascii() or not digits.isdigit() or not 9 <= len(digits) <= 15 or digits.startswith("0"):
            raise ValidationError("Enter a valid international phone number.")
        return compact
    if not compact.isdigit():
        raise ValidationError("Enter a valid phone number.")
    if len(compact) == 10 and compact.startswith("0"):
        return "+233" + compact[1:]
    if len(compact) == 9:
        return "+233" + compact
    if len(compact) == 12 and compact.startswith("233"):
        return "+" + compact
    raise ValidationError("Use a Ghana local number or include the international + country code.")


def identity_keys(person):
    if person.is_anonymised:
        return {"identity_email": "", "identity_phone": "", "identity_number": "", "identity_name": ""}
    try:
        phone = normalize_phone(person.phone) if person.age_band != "under_18" else ""
    except ValidationError:
        phone = ""  # Legacy malformed contact data must never be an identity match.
    return {
        "identity_email": normalized_text(person.email).casefold(),
        "identity_phone": phone,
        "identity_number": normalize_number(person.staff_or_student_no),
        "identity_name": normalize_name(person.full_name),
    }
