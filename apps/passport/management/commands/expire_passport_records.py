from django.core.management.base import BaseCommand

from apps.passport.services import expire_due_records


class Command(BaseCommand):
    help = "Mark expired passport credentials and evidence as expired."

    def handle(self, *args, **options):
        result = expire_due_records()
        self.stdout.write(
            self.style.SUCCESS(
                "Expired %(credentials)s credential(s) and %(evidence)s evidence record(s)."
                % result
            )
        )
