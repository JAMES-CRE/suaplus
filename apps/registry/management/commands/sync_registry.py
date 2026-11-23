from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.registry.sync import RegistrySyncError, sync_from_registry


class Command(BaseCommand):
    help = "Sync staff and student records from the configured HR/Registry API."

    def handle(self, *args, **options):
        try:
            result = sync_from_registry()
        except (ImproperlyConfigured, RegistrySyncError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "Registry sync complete: "
                f"{result['created']} created, {result['updated']} updated, "
                f"{result['skipped']} skipped, {result['conflicts']} queued for identity review."
            )
        )
