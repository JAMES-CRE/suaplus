"""Adapter for syncing staff and student records from HR/Registry."""

import os
import requests
from django.conf import settings
from apps.registry.models import Person, OrgUnit

def sync_from_registry():
    """Fetch staff and students from the KNUST Registry API (placeholder)."""
    api_url = os.environ.get("HR_SYNC_URL", "")
    api_key = os.environ.get("HR_SYNC_KEY", "")

    if not api_url or not api_key:
        print("⚠️ HR_SYNC_URL or HR_SYNC_KEY not configured. Skipping sync.")
        return

    try:
        response = requests.get(
            api_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        created_count = 0
        updated_count = 0

        for record in data.get("staff", []) + data.get("students", []):
            email = record.get("email", "").lower()
            if not email:
                continue

            # Find or create the person
            person, created = Person.objects.update_or_create(
                email=email,
                defaults={
                    "full_name": record.get("full_name", ""),
                    "staff_or_student_no": record.get("id_number", ""),
                    "affiliation": record.get("affiliation", "staff"),
                    "category": record.get("category", ""),
                    "rank_or_level": record.get("rank", ""),
                }
            )

            if created:
                created_count += 1
            else:
                updated_count += 1

        print(f"✅ Sync complete: {created_count} created, {updated_count} updated.")

    except Exception as e:
        print(f"❌ Registry sync failed: {e}")