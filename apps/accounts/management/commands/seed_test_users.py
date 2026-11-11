"""Seed test Hub users for every role.

Usage:
    python manage.py seed_test_users

Creates one user per role, all with password 'testpass123'.
Safe to re-run - uses get_or_create.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from apps.accounts.models import RoleAssignment, UserAccount
from apps.programmes.models import Programme
from apps.registry.models import OrgUnit, Person


TEST_USERS = [
    # (username, full_name, role, scope_type, scope_source)
    # scope_source is either 'all', 'unit', 'programme', or 'organisation'
    ("test_admin",     "Test Admin",        "super_admin",        "all",         None),
    ("test_exec",      "Test Executive",    "executive_viewer",   "all",         None),
    ("test_owner",     "Test Owner",        "programme_owner",    "programme",   "programme"),
    ("test_facil",     "Test Facilitator",  "facilitator",        "programme",   "programme"),
    ("test_focal",     "Test Focal",        "focal_person",       "unit",        "unit"),
    ("test_curator",   "Test Curator",      "library_curator",    "all",         None),
    ("test_norole",    "Test No Role",      None,                 None,          None),
]


class Command(BaseCommand):
    help = "Seed test Hub users for every role"

    def handle(self, *args, **options):
        User = get_user_model()

        # Get a programme and a college for scope
        programme = Programme.objects.first()
        college = OrgUnit.objects.filter(type="college").first()

        if not programme:
            self.stdout.write(self.style.ERROR("No programme found. Create one in admin first."))
            return
        if not college:
            self.stdout.write(self.style.ERROR("No college OrgUnit found. Create one in admin first."))
            return

        self.stdout.write(f"Using programme: {programme.name}")
        self.stdout.write(f"Using college: {college.name}\n")

        for username, full_name, role, scope_type, scope_source in TEST_USERS:
            email = f"{username}@knust.edu.gh"

            # 1. Create or get Person
            person, _ = Person.objects.get_or_create(
                email=email,
                defaults={
                    "affiliation": "staff",
                    "full_name": full_name,
                    "title": "Dr.",
                    "category": "academic",
                    "unit": college,
                },
            )

            # 2. Create or get User
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"email": email},
            )
            if created:
                user.set_password("testpass123")
                user.save()

            # 3. Create UserAccount
            account, _ = UserAccount.objects.get_or_create(
                user=user,
                defaults={"person": person, "auth_method": "password"},
            )

            # 4. Create RoleAssignment if role is set
            if role:
                scope_id = None
                if scope_source == "programme":
                    scope_id = programme.id
                elif scope_source == "unit":
                    scope_id = college.id

                RoleAssignment.objects.get_or_create(
                    user_account=account,
                    role=role,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )

            status = "created" if created else "exists"
            role_label = role or "(no role)"
            self.stdout.write(
                self.style.SUCCESS(
                    f"  {username:15} → {role_label:20} (scope: {scope_type or '-'}) [{status}]"
                )
            )

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("All test users ready."))
        self.stdout.write("Password for all: testpass123")
        self.stdout.write("=" * 60)
        