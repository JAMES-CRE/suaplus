"""Management command: run the tier engine."""

from django.core.management.base import BaseCommand

from apps.programmes.models import Programme
from apps.programmes.services.tier_engine import compute_tier_status


class Command(BaseCommand):
    help = "Compute trainer tier statuses from evidence and criteria"

    def add_arguments(self, parser):
        parser.add_argument(
            "--programme", type=int, help="Restrict to a single programme ID"
        )

    def handle(self, *args, **options):
        programme = None
        if options["programme"]:
            programme = Programme.objects.get(id=options["programme"])

        summary = compute_tier_status(programme)
        self.stdout.write(self.style.SUCCESS(f"Tier engine complete: {summary}"))