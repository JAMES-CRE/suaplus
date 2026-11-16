"""Adapter for syncing staff and student records from the HR/Registry API.

The adapter expects a JSON object with ``staff`` and ``students`` arrays. Each
record must contain ``email`` and ``full_name``; optional supported fields are
``id_number``, ``category`` and ``rank``. Phone numbers are deliberately not
imported because the registry feed must not populate participant contact data.
"""

import os

import requests
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction

from apps.registry.models import Person


class RegistrySyncError(Exception):
    """Raised when the configured Registry endpoint cannot be safely imported."""


def sync_from_registry(*, api_url=None, api_key=None, http_get=None):
    """Fetch the Registry feed and upsert people by email.

    Optional arguments make the adapter straightforward to exercise without a
    live Registry service. Invalid/incomplete records are counted as skipped;
    unexpected API payloads and database conflicts fail the whole sync.
    """
    api_url = api_url if api_url is not None else os.environ.get("HR_SYNC_URL", "").strip()
    api_key = api_key if api_key is not None else os.environ.get("HR_SYNC_KEY", "").strip()
    if not api_url or not api_key:
        raise ImproperlyConfigured("Set both HR_SYNC_URL and HR_SYNC_KEY before running Registry sync.")

    get = http_get or requests.get
    try:
        response = get(
            api_url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RegistrySyncError(f"Registry request failed: {exc}") from exc

    if not isinstance(data, dict) or any(
        key in data and not isinstance(data[key], list) for key in ("staff", "students")
    ):
        raise RegistrySyncError("Registry response must be an object containing staff and students arrays.")

    created_count = updated_count = skipped_count = 0
    try:
        with transaction.atomic():
            for affiliation in ("staff", "student"):
                records = data.get("staff" if affiliation == "staff" else "students", [])
                for record in records:
                    if not isinstance(record, dict):
                        skipped_count += 1
                        continue
                    email = str(record.get("email") or "").strip().lower()
                    full_name = str(record.get("full_name") or "").strip()
                    if not email or not full_name:
                        skipped_count += 1
                        continue

                    matches = Person.objects.filter(email__iexact=email)
                    if matches.count() > 1:
                        raise RegistrySyncError(f"Multiple participant records match Registry email {email}.")
                    person = matches.first()
                    if person and person.is_anonymised:
                        skipped_count += 1
                        continue

                    values = {
                        "email": email,
                        "full_name": full_name,
                        "affiliation": affiliation,
                    }
                    if "id_number" in record:
                        values["staff_or_student_no"] = str(record.get("id_number") or "").strip() or None
                    if "category" in record:
                        values["category"] = str(record.get("category") or "").strip()
                    if "rank" in record:
                        values["rank_or_level"] = str(record.get("rank") or "").strip()

                    try:
                        if person:
                            for field, value in values.items():
                                setattr(person, field, value)
                            person.full_clean(exclude=["organisation", "unit", "merged_into"])
                            person.save(update_fields=[*values.keys(), "updated_at"])
                            updated_count += 1
                        else:
                            person = Person(**values)
                            person.full_clean(exclude=["organisation", "unit", "merged_into"])
                            person.save()
                            created_count += 1
                    except (IntegrityError, ValidationError) as exc:
                        raise RegistrySyncError(f"Could not import Registry record for {email}: {exc}") from exc
    except IntegrityError as exc:
        raise RegistrySyncError(f"Registry sync rolled back because of a database conflict: {exc}") from exc

    return {
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "total": created_count + updated_count + skipped_count,
    }
