"""Registry adapter: canonical matching, per-record atomic updates, review conflicts.

Real credentials remain optional until supplied. Phone fields from the feed are
never imported. A conflicting identity is queued, not silently overwritten.
"""
import os

import requests
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction

from apps.registry.identity import identity_data, resolve_or_create
from apps.registry.workflows import _registry_update


class RegistrySyncError(Exception):
    """The configured Registry endpoint cannot be safely imported."""


def sync_from_registry(*, api_url=None, api_key=None, http_get=None):
    api_url = api_url if api_url is not None else os.environ.get("HR_SYNC_URL", "").strip()
    api_key = api_key if api_key is not None else os.environ.get("HR_SYNC_KEY", "").strip()
    if not api_url or not api_key:
        raise ImproperlyConfigured("Set both HR_SYNC_URL and HR_SYNC_KEY before running Registry sync.")
    try:
        response = (http_get or requests.get)(
            api_url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}, timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RegistrySyncError(f"Registry request failed: {exc}") from exc
    if not isinstance(data, dict) or any(key in data and not isinstance(data[key], list) for key in ("staff", "students")):
        raise RegistrySyncError("Registry response must contain staff and students arrays.")
    summary = {"created": 0, "updated": 0, "skipped": 0, "conflicts": 0, "total": 0}
    for affiliation in ("staff", "student"):
        for record in data.get("staff" if affiliation == "staff" else "students", []):
            summary["total"] += 1
            if not isinstance(record, dict) or not record.get("email") or not record.get("full_name"):
                summary["skipped"] += 1
                continue
            try:
                submitted = identity_data(
                    email=record["email"], full_name=record["full_name"], affiliation=affiliation,
                    staff_or_student_no=record.get("id_number", ""),
                )
                values = {"email": submitted["email"], "full_name": submitted["full_name"], "affiliation": affiliation}
                if submitted["staff_or_student_no"]:
                    values["staff_or_student_no"] = submitted["staff_or_student_no"]
                if "category" in record:
                    values["category"] = str(record["category"] or "").strip()
                if "rank" in record:
                    values["rank_or_level"] = str(record["rank"] or "").strip()
                with transaction.atomic():
                    result = resolve_or_create(submitted, source="registry", context={"values": values})
                    if result.status == "conflict":
                        summary["conflicts"] += 1
                        continue
                    if result.status == "error":
                        summary["skipped"] += 1
                        continue
                    _registry_update(result.person, values)
                    summary["created" if result.created else "updated"] += 1
            except ValidationError:
                summary["skipped"] += 1
            except IntegrityError as exc:
                raise RegistrySyncError("A Registry record failed its database integrity checks.") from exc
    return summary
