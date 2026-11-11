from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import UserAccount
from apps.audit.models import AuditEvent
from apps.notifications.models import Notification
from apps.passport.models import (
    CorrectionRequest,
    Credential,
    CredentialReview,
    Evidence,
    SelfAssessment,
)
from apps.programmes.models import (
    Nomination,
    Partner,
    Programme,
    ProgrammeCompetency,
    ProgrammeOwner,
    ProgrammePartner,
    TierCriterion,
    TierStatus,
)
from apps.registry.models import (
    Consent,
    Organisation,
    OrgUnit,
    PassportToken,
    Person,
    UnitHeadcount,
)
from apps.reporting.models import Memo, Target
from apps.sessions.models import (
    Attendance,
    Facilitation,
    ImportBatch,
    Session,
    SurveyResponse,
)


class Command(BaseCommand):
    help = "Wipes all demo data but keeps the Super Admin and test users."

    def handle(self, *args, **options):
        self.stdout.write("Starting database wipe...")

        with transaction.atomic():
            # 1. Delete attendance, evidence, surveys
            self.stdout.write("Deleting attendance, evidence, and surveys...")
            SurveyResponse.objects.all().delete()
            Attendance.objects.all().delete()
            Evidence.objects.all().delete()
            Facilitation.objects.all().delete()

            # 2. Delete sessions, imports
            self.stdout.write("Deleting sessions and imports...")
            ImportBatch.objects.all().delete()
            Session.objects.all().delete()

            # 3. Delete credentials and self-assessments
            self.stdout.write("Deleting credentials and self-assessments...")
            CredentialReview.objects.all().delete()
            Credential.objects.all().delete()
            SelfAssessment.objects.all().delete()
            CorrectionRequest.objects.all().delete()

            # 4. Delete programmes, partners, criteria
            self.stdout.write("Deleting programmes, partners, and nominations...")
            Nomination.objects.all().delete()
            TierStatus.objects.all().delete()
            TierCriterion.objects.all().delete()
            ProgrammeCompetency.objects.all().delete()
            ProgrammeOwner.objects.all().delete()
            ProgrammePartner.objects.all().delete()
            Partner.objects.all().delete()
            Programme.objects.all().delete()

            # 5. Delete registry data
            self.stdout.write("Deleting registry data (people, orgs, units)...")
            PassportToken.objects.all().delete()
            Consent.objects.all().delete()
            UnitHeadcount.objects.all().delete()
            Organisation.objects.all().delete()
            OrgUnit.objects.all().delete()

            # Unlink people from UserAccounts before deleting them
            UserAccount.objects.all().update(person=None)
            Person.objects.all().delete()

            # 6. Delete reports, targets
            self.stdout.write("Deleting memos and targets...")
            Memo.objects.all().delete()
            Target.objects.all().delete()

            # 7. Delete audit logs and notifications
            self.stdout.write("Deleting audit logs and notifications...")
            AuditEvent.objects.all().delete()
            Notification.objects.all().delete()

            # 8. Re-create blank Person records for surviving Hub users
            self.stdout.write("Re-creating blank Person records for existing Hub users...")
            for account in UserAccount.objects.all():
                new_person = Person.objects.create(
                    affiliation="staff",
                    email=account.user.email,
                    full_name=account.user.get_full_name() or account.user.username,
                    category="academic",
                )
                account.person = new_person
                account.save()

        self.stdout.write(
            self.style.SUCCESS(
                "Database wiped successfully. Super Admin and test users preserved."
            )
        )