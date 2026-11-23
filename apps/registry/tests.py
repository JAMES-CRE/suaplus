from unittest.mock import Mock

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from apps.registry.models import Person
from apps.registry.sync import RegistrySyncError, sync_from_registry


class RegistrySyncTests(TestCase):
    def test_sync_creates_updates_and_skips_invalid_records(self):
        existing = Person.objects.create(
            affiliation="staff", email="old@example.com", full_name="Old Name"
        )
        response = Mock()
        response.json.return_value = {
            "staff": [
                {"email": "new@example.com", "full_name": "New Person", "id_number": "S-1"},
                {"email": "old@example.com", "full_name": "Updated Name", "rank": "Senior"},
                {"email": "", "full_name": "Missing Email"},
            ],
            "students": [{"email": "student@example.com", "full_name": "Student"}],
        }
        response.raise_for_status.return_value = None
        get = Mock(return_value=response)

        result = sync_from_registry(
            api_url="https://registry.example/api", api_key="test-key", http_get=get
        )

        self.assertEqual(result, {"created": 2, "updated": 1, "skipped": 1, "conflicts": 0, "total": 4})
        existing.refresh_from_db()
        self.assertEqual(existing.full_name, "Updated Name")
        self.assertEqual(existing.rank_or_level, "Senior")
        self.assertTrue(Person.objects.filter(email="new@example.com", affiliation="staff").exists())
        self.assertTrue(Person.objects.filter(email="student@example.com", affiliation="student").exists())
        self.assertEqual(get.call_args.kwargs["timeout"], 30)

    def test_sync_refuses_to_overwrite_an_anonymised_record(self):
        Person.objects.create(
            affiliation="staff", email="erased@example.com", full_name="Erased",
            is_anonymised=True,
        )
        response = Mock()
        response.json.return_value = {
            "staff": [{"email": "erased@example.com", "full_name": "Registry Name"}],
            "students": [],
        }
        response.raise_for_status.return_value = None

        result = sync_from_registry(
            api_url="https://registry.example/api", api_key="key",
            http_get=Mock(return_value=response),
        )

        self.assertEqual(result["skipped"], 1)
        self.assertEqual(Person.objects.get(email="erased@example.com").full_name, "Erased")

    def test_sync_requires_external_service_configuration(self):
        with self.assertRaises(ImproperlyConfigured):
            sync_from_registry(api_url="", api_key="")

    def test_sync_rejects_unexpected_payload_shape(self):
        response = Mock()
        response.json.return_value = {"staff": {"email": "wrong-shape"}}
        response.raise_for_status.return_value = None

        with self.assertRaises(RegistrySyncError):
            sync_from_registry(
                api_url="https://registry.example/api", api_key="key",
                http_get=Mock(return_value=response),
            )
