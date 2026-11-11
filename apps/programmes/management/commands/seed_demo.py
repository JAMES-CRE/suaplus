"""Seed demo data - framework, competency, levels, evidence for one person.

Use this once to create the minimum data the tier engine needs to run
against a real test case.

Usage:
    python manage.py seed_demo
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.library.models import Competency, Domain, Framework, Level
from apps.passport.models import Evidence
from apps.programmes.models import Programme, TierCriterion
from apps.registry.models import Person
from apps.sessions.models import Attendance


class Command(BaseCommand):
    help = "Seed demo data so the tier engine has something to evaluate"

    def handle(self, *args, **options):
        # 1. Framework
        framework, _ = Framework.objects.get_or_create(
            code="KNUST",
            defaults={
                "name": "KNUST Competency Framework",
                "is_knust": True,
                "status": "active",
            },
        )
        self.stdout.write(f"Framework: {framework.code}")

        # 2. Levels 1-5
        for ordinal, label in [
            (1, "Aware"),
            (2, "Developing"),
            (3, "Proficient"),
            (4, "Advanced"),
            (5, "Leading"),
        ]:
            Level.objects.get_or_create(
                framework=framework,
                ordinal=ordinal,
                defaults={"label": label},
            )
        self.stdout.write(f"Levels: {Level.objects.filter(framework=framework).count()}")

        # 3. Domain + Competency
        domain, _ = Domain.objects.get_or_create(
            framework=framework,
            code="AI",
            defaults={"name": "AI Competency", "display_order": 1},
        )
        competency, _ = Competency.objects.get_or_create(
            framework=framework,
            code="AI-01",
            defaults={
                "domain": domain,
                "title": "AI foundations and tools",
                "status": "active",
            },
        )
        self.stdout.write(f"Competency: {competency.code} - {competency.title}")

        # 4. Programme - the one with attendance
        programme = Programme.objects.filter(name="ai").first() or Programme.objects.first()
        if not programme:
            self.stdout.write(self.style.ERROR("No programme found"))
            return
        self.stdout.write(f"Programme: {programme.name} (id={programme.id})")

        # 5. Attach competency to programme
        from apps.programmes.models import ProgrammeCompetency

        ProgrammeCompetency.objects.get_or_create(
            programme=programme,
            competency=competency,
            defaults={"target_level": 2},
        )
        self.stdout.write(f"ProgrammeCompetency: {competency.code} → {programme.name}")

        # 6. Criterion - tier 2 needs level 1 on AI-01
        TierCriterion.objects.filter(programme=programme, tier=2).delete()
        TierCriterion.objects.create(
            programme=programme,
            tier=2,
            rule_type="min_level",
            parameters={"competency": "AI-01", "level": 1},
        )
        self.stdout.write(f"TierCriterion: tier 2 = level 1 on AI-01")

        # 7. Evidence for the person who has attendance
        attendance = Attendance.objects.first()
        if attendance:
            person = attendance.person
            level1 = Level.objects.get(framework=framework, ordinal=1)
            Evidence.objects.get_or_create(
                person=person,
                competency=competency,
                level=level1,
                type="external",
                source_type="credential",
                defaults={"status": "valid"},
            )
            self.stdout.write(
                f"Evidence: {person.full_name} - {competency.code} @ L1 (external)"
            )
        else:
            self.stdout.write(self.style.WARNING("No attendance found - no evidence created"))

        self.stdout.write(self.style.SUCCESS("Seed complete"))