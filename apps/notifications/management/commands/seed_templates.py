"""Seed default notification templates."""

from django.core.management.base import BaseCommand
from apps.notifications.models import NotificationTemplate

TEMPLATES = [
    {
        "key": "passport_link",
        "channel": "email",
        "subject": "Your Sua+ Passport Link",
        "body": (
            "Hello {name},\n\n"
            "Thank you for attending {session_title}.\n\n"
            "Your Sua+ passport has been updated. Click the link below to view it:\n"
            "{passport_url}\n\n"
            "Learn. Lead. Multiply.\n"
            "The Sua+ Team"
        ),
    },
    {
        "key": "nomination_pending",
        "channel": "email",
        "subject": "Action Required: Trainer Nomination",
        "body": (
            "Hello,\n\n"
            "{nominator_name} has nominated {person_name} for the {tier} tier "
            "in the {programme_name} programme.\n\n"
            "Please log in to review this nomination:\n"
            "{review_url}\n\n"
            "The Sua+ Team"
        ),
    },
    {
        "key": "nomination_approved",
        "channel": "email",
        "subject": "Congratulations! Your Trainer Tier was Approved",
        "body": (
            "Hello {name},\n\n"
            "Your nomination for the {tier} tier in the {programme_name} programme "
            "has been approved.\n\n"
            "You can now create sessions and train others. Log in here:\n"
            "{login_url}\n\n"
            "The Sua+ Team"
        ),
    },
    {
        "key": "credential_approved",
        "channel": "email",
        "subject": "Your Credential was Approved",
        "body": (
            "Hello {name},\n\n"
            "Your submitted credential, '{credential_title}', has been approved and "
            "added to your Sua+ passport.\n\n"
            "View your passport:\n"
            "{passport_url}\n\n"
            "The Sua+ Team"
        ),
    },
    
    
        {
        "key": "followup_survey",
        "channel": "email",
        "subject": "How are you applying what you learned?",
        "body": (
            "Hello {name},\n\n"
            "90 days ago, you attended {session_title}.\n\n"
            "We'd love to know how you are applying the skills you learned. "
            "Please take 2 minutes to complete this follow-up survey:\n"
            "{survey_url}\n\n"
            "The Sua+ Team"
        ),
    },
    
    
]

class Command(BaseCommand):
    help = "Seed default notification templates"

    def handle(self, *args, **options):
        created_count = 0
        for t in TEMPLATES:
            obj, created = NotificationTemplate.objects.update_or_create(
                key=t["key"],
                defaults={
                    "channel": t["channel"],
                    "subject": t["subject"],
                    "body": t["body"],
                    "active": True,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(f"  + Created template '{t['key']}'")
            else:
                self.stdout.write(f"  ~ Updated template '{t['key']}'")

        self.stdout.write(self.style.SUCCESS(f"Seeded {created_count} new templates."))