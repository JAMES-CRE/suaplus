"""Management command to generate missing Open Badges."""

from django.core.management.base import BaseCommand
from apps.passport.models import Credential
from apps.library.models import CertificationCatalogue
from apps.passport.services import generate_badge_json

class Command(BaseCommand):
    help = "Generate Open Badges for approved credentials"

    def handle(self, *args, **options):
        creds = Credential.objects.filter(review_status="approved", badge_json__isnull=True)
        self.stdout.write(f"Found {creds.count()} credentials needing badges.")

        success_count = 0
        for c in creds:
            catalogue = CertificationCatalogue.objects.filter(
                issuer__iexact=c.issuer, 
                title__iexact=c.title
            ).first()
            
            if not catalogue:
                self.stdout.write(self.style.WARNING(f"Skipped {c.title}: No matching catalogue entry."))
                continue
                
            first_mapping = catalogue.competencies.first()
            if not first_mapping:
                self.stdout.write(self.style.WARNING(f"Skipped {c.title}: Catalogue has no competency mappings."))
                continue

            
            
            
            from apps.library.models import Level
            level_obj = Level.objects.filter(framework__is_knust=True, ordinal=first_mapping.max_level).first()
            if not level_obj:
                self.stdout.write(self.style.WARNING(f"Skipped {c.title}: Level {first_mapping.max_level} not found."))
                continue

            badge_jwt = generate_badge_json(c.person, first_mapping.competency, level_obj)
            
            c.badge_json = {"jwt": badge_jwt}
            c.catalogue = catalogue
            c.save()
            success_count += 1
            self.stdout.write(self.style.SUCCESS(f"Generated badge for {c.title}!"))

        self.stdout.write(self.style.SUCCESS(f"Done. Generated {success_count} badges."))
