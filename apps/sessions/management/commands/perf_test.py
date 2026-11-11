"""Performance test: Simulate 500 check-ins and measure the time."""

import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client
from django.utils import timezone

from apps.programmes.models import Programme
from apps.registry.models import Person
from apps.sessions.models import Attendance, Session


class Command(BaseCommand):
    help = "Simulate 500 check-ins and measure the time (NFR-07)"

    def handle(self, *args, **options):
        # 1. Setup: Create a session with a UNIQUE code
        programme = Programme.objects.first()
        if not programme:
            self.stdout.write(self.style.ERROR("No programme found. Create one in admin first."))
            return

        # Bypass rate limiting for this test
        settings.BYPASS_RATE_LIMIT = True

        # Generate a random 6-char code so we never clash
        unique_code = uuid.uuid4().hex[:6].upper()

        session = Session.objects.create(
            programme=programme,
            title="Performance Test Session",
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=2),
            mode="in_person",
            session_code=unique_code,
            status="open",
        )
        
        # 2. Setup: Create 500 people
        self.stdout.write("Creating 500 test people...")
        people = []
        for i in range(500):
            people.append(
                Person(
                    affiliation="student",
                    full_name=f"Perf Test {i}",
                    email=f"perf_{uuid.uuid4().hex[:8]}@st.knust.edu.gh",
                )
            )
        Person.objects.bulk_create(people, ignore_conflicts=True)
        
        # Fetch them back to get IDs
        test_people = Person.objects.filter(full_name__startswith="Perf Test")
        self.stdout.write(f"Created {test_people.count()} people.")

        # 3. Simulate check-ins
        client = Client()
        start_time = time.time()
        success_count = 0
        error_count = 0

        self.stdout.write("Simulating 500 check-ins...")
        for person in test_people:
            try:
                response = client.post(
                    f"/checkin/{session.session_code}/",
                    {
                        "affiliation": "student",
                        "full_name": person.full_name,
                        "email": person.email,
                        "staff_or_student_no": "",
                        "phone": "",
                        "organisation_name": "",
                        "consent": "on",
                        "age_band": "18_plus", # <-- ADDED: Required by the updated CheckinForm
                    },
                    HTTP_HOST="localhost",  # Force localhost to avoid testserver rejection
                )
                if response.status_code == 302:
                    success_count += 1
                else:
                    error_count += 1
            except Exception:
                error_count += 1

        end_time = time.time()
        duration = end_time - start_time

        # 4. Report
        self.stdout.write(self.style.SUCCESS(f"\n=== RESULTS ==="))
        self.stdout.write(f"Total check-ins: 500")
        self.stdout.write(f"Successful: {success_count}")
        self.stdout.write(f"Errors: {error_count}")
        self.stdout.write(f"Time taken: {duration:.2f} seconds")
        
        # 5. Cleanup
        self.stdout.write("Cleaning up test session and people...")
        Attendance.objects.filter(session=session).delete()
        session.delete()
        test_people.delete()
        
        if duration <= 120:
            self.stdout.write(self.style.SUCCESS("✅ PASS: Under 2 minutes (NFR-07)"))
        else:
            self.stdout.write(self.style.ERROR("❌ FAIL: Over 2 minutes (NFR-07)"))
            
        # Restore rate limiting
        settings.BYPASS_RATE_LIMIT = False