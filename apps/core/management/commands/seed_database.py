import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.library.models import (
    Framework, Domain, Competency, Level, CertificationCatalogue, CertificationCompetency
)
from apps.programmes.models import (
    Programme, ProgrammeCompetency, TierCriterion, Partner, Organisation
)
from apps.core.models import AcademicPeriod
from apps.sessions.models import SurveyTemplate

class Command(BaseCommand):
    help = "Seeds the database with the foundational data from SRS Annex A"

    def handle(self, *args, **options):
        self.stdout.write("Starting database seed...")

        with transaction.atomic():
            # 1. Create Framework
            framework, _ = Framework.objects.get_or_create(
                code="KNUST",
                defaults={"name": "KNUST Competency Framework", "is_knust": True}
            )
            self.stdout.write("Created Framework: KNUST")

            # 2. Create Levels (SRS 2.2)
            levels_data = [
                (1, "Aware", "Knows what it is and why it matters"),
                (2, "Developing", "Uses it with guidance"),
                (3, "Proficient", "Uses it independently and appropriately"),
                (4, "Advanced", "Adapts it and supports colleagues"),
                (5, "Leading", "Innovates, leads and trains others"),
            ]
            for ordinal, label, desc in levels_data:
                Level.objects.get_or_create(
                    framework=framework, ordinal=ordinal,
                    defaults={"label": label, "descriptor": desc}
                )
            self.stdout.write("Created 5 Levels")

            # 3. Create Domains
            domains_data = [
                ("AI", "AI Competency", 1),
                ("DC", "Digital Competency", 2),
                ("DE", "Digital Education", 3),
                ("ID", "Instructional Design", 4),
                ("FT", "Facilitation and Training", 5),
            ]
            domains = {}
            for code, name, order in domains_data:
                domain, _ = Domain.objects.get_or_create(
                    framework=framework, code=code,
                    defaults={"name": name, "display_order": order}
                )
                domains[code] = domain
            self.stdout.write("Created 5 Domains")

            # 4. Create 28 Competencies (SRS Annex A.2)
            comps_data = [
                ("AI-01", "AI", "AI foundations and tools", "Explains how generative AI works and where it fails; picks the right tool for a task"),
                ("AI-02", "AI", "Effective prompting and grounding", "Writes, refines and grounds prompts in source documents to get reliable output"),
                ("AI-03", "AI", "Ethics, privacy and responsible use", "Applies ethical principles, data-privacy rules and KNUST policy whenever AI is used"),
                ("AI-04", "AI", "Human agency and critical evaluation", "Checks output for accuracy, bias and hallucination; remains accountable for decisions"),
                ("AI-05", "AI", "AI in teaching, learning and assessment", "Integrates AI into course design and assessment while protecting academic integrity"),
                ("AI-06", "AI", "AI for learning, research and productivity", "Uses tools such as Deep Research and NotebookLM to study, research and work efficiently and transparently"),
                ("AI-07", "AI", "Creating with AI", "Builds reusable assistants (e.g. Gems) and AI-supported resources; students scope and design simple AI solutions"),
                ("AI-08", "AI", "Shaping AI policy and culture", "Contributes to departmental and institutional AI guidelines and leads responsible adoption"),
                ("DC-01", "DC", "Information and data literacy", "Finds, evaluates and manages information and data, judging source credibility"),
                ("DC-02", "DC", "Communication and collaboration", "Collaborates online across tools and teams; manages professional digital identity"),
                ("DC-03", "DC", "Digital content creation", "Produces and edits content, respecting copyright and licences"),
                ("DC-04", "DC", "Safety, wellbeing and responsible use", "Protects devices, data and privacy; recognises cyber threats and misinformation"),
                ("DC-05", "DC", "Problem solving and digital learning", "Solves routine technical problems and keeps skills current"),
                ("DE-01", "DE", "Digital professional engagement", "Uses digital tools for communication, collaboration, reflection and own development"),
                ("DE-02", "DE", "Digital learning resources", "Selects, creates, adapts and shares resources legally and safely"),
                ("DE-03", "DE", "Online and blended teaching", "Plans and runs blended or online sessions that guide, connect and build learner independence"),
                ("DE-04", "DE", "Digital assessment and feedback", "Uses digital assessment, analyses learner data and gives timely feedback"),
                ("DE-05", "DE", "Inclusion, personalisation and engagement", "Makes learning accessible, differentiated and active"),
                ("DE-06", "DE", "Developing learners' digital and AI competence", "Builds students' information, communication, creation, responsible-use and AI skills into courses"),
                ("ID-01", "ID", "Needs and learner analysis", "Analyses learners, context and constraints before designing"),
                ("ID-02", "ID", "Measurable outcomes and alignment", "Writes measurable outcomes and aligns assessments and activities to them"),
                ("ID-03", "ID", "Course structure and orientation", "Gives a clear course start, navigation and learner support information"),
                ("ID-04", "ID", "Materials, activities and interaction", "Designs materials and activities that drive interaction and meet outcomes"),
                ("ID-05", "ID", "Technology, accessibility and usability", "Chooses course technology that works for all learners, including those with disabilities"),
                ("ID-06", "ID", "Implementation, evaluation and design project management", "Runs a design project to schedule, evaluates the course and improves it from evidence"),
                ("FT-01", "FT", "Designing and facilitating workshops", "Plans and runs an engaging session that meets its competency targets and CSAT 4.0 or higher"),
                ("FT-02", "FT", "Mentoring and coaching colleagues", "Guides an Associate Trainer to independent facilitation"),
                ("FT-03", "FT", "Cascading and community building", "Organises follow-on sessions and sustains a practice community in their unit"),
            ]
            competencies = {}
            for code, domain_code, title, descriptor in comps_data:
                comp, _ = Competency.objects.get_or_create(
                    framework=framework, code=code,
                    defaults={
                        "domain": domains[domain_code],
                        "title": title,
                        "proficient_descriptor": descriptor,
                        "status": "active"
                    }
                )
                competencies[code] = comp
            self.stdout.write("Created 28 Competencies")

            # 5. Create Organisations
            knust_org, _ = Organisation.objects.get_or_create(
                name="KNUST E-Learning Centre", defaults={"type": "university"}
            )
            google_org, _ = Organisation.objects.get_or_create(
                name="Google for Education", defaults={"type": "company"}
            )
            self.stdout.write("Created Organisations")

            # 6. Create the Gemini for Faculty ToT Programme
            programme, _ = Programme.objects.get_or_create(
                name="Gemini for Education - Faculty ToT",
                defaults={
                    "description": "Faculty programme mapping chiefly to the AI domain",
                    "status": "active"
                }
            )
            self.stdout.write("Created Programme: Gemini for Faculty")

            # 7. Map Programme Competencies (SRS A.4.2)
            programme_mappings = {
                "AI-01": 3, "AI-02": 3, "AI-03": 3, "AI-04": 3,
                "AI-05": 2, "AI-06": 2, "AI-07": 2,
                "DE-04": 2, "DE-06": 2, "ID-04": 2,
                "FT-01": 3, "FT-02": 2, "FT-03": 2,
            }
            for code, target in programme_mappings.items():
                ProgrammeCompetency.objects.get_or_create(
                    programme=programme, competency=competencies[code],
                    defaults={"target_level": target}
                )
            self.stdout.write("Mapped Competencies to Programme")

            # 8. Create Tier Criteria (SRS A.4.3)
            # Clear existing criteria for this programme first
            TierCriterion.objects.filter(programme=programme).delete()
            
            # Tier 2: Practitioner
            TierCriterion.objects.create(
                programme=programme, tier=2, rule_type="min_level",
                parameters={"competency": "AI-01", "level": 2}
            )
            TierCriterion.objects.create(
                programme=programme, tier=2, rule_type="min_level",
                parameters={"competency": "AI-02", "level": 2}
            )
            TierCriterion.objects.create(
                programme=programme, tier=2, rule_type="min_level",
                parameters={"competency": "AI-03", "level": 2}
            )
            TierCriterion.objects.create(
                programme=programme, tier=2, rule_type="min_level",
                parameters={"competency": "AI-04", "level": 2}
            )

            # Tier 3: Associate Trainer
            TierCriterion.objects.create(
                programme=programme, tier=3, rule_type="required_credential",
                parameters={"title": "Gemini Certified"}
            )

            # Tier 4: Certified Trainer
            TierCriterion.objects.create(
                programme=programme, tier=4, rule_type="required_credential",
                parameters={"title": "Gemini Certified"}
            )
            TierCriterion.objects.create(
                programme=programme, tier=4, rule_type="min_level",
                parameters={"competency": "AI-05", "level": 3, "evidence": ["assessor", "artefact"]}
            )
            TierCriterion.objects.create(
                programme=programme, tier=4, rule_type="min_sessions",
                parameters={"sessions": 2}
            )
            TierCriterion.objects.create(
                programme=programme, tier=4, rule_type="min_mean_csat",
                parameters={"threshold": 4.0}
            )
            
            self.stdout.write("Created Tier Criteria")

            # 9. Create Sanctioned Certifications (SRS A.3.2)
            cat, _ = CertificationCatalogue.objects.get_or_create(
                issuer="Google for Education",
                title="Gemini Certified Faculty",
                defaults={"verification_method": "Digital certificate link", "active": True}
            )
            cert_mappings = {
                "AI-01": 3, "AI-02": 3, "AI-03": 3, "AI-04": 3,
                "AI-06": 3, "AI-05": 2
            }
            for code, max_level in cert_mappings.items():
                CertificationCompetency.objects.get_or_create(
                    certification=cat, competency=competencies[code],
                    defaults={"max_level": max_level}
                )
            
            cat_edu, _ = CertificationCatalogue.objects.get_or_create(
                issuer="Google for Education",
                title="Gemini Certified Educator",
                defaults={"verification_method": "Digital certificate link", "active": True}
            )
            for code, max_level in cert_mappings.items():
                CertificationCompetency.objects.get_or_create(
                    certification=cat_edu, competency=competencies[code],
                    defaults={"max_level": max_level}
                )
            self.stdout.write("Created Sanctioned Certifications")

            # 10. Create Academic Period
            AcademicPeriod.objects.get_or_create(
                year_start=2025, year_end=2026, semester=1,
                defaults={
                    "start_date": datetime.date(2025, 9, 1),
                    "end_date": datetime.date(2026, 1, 31)
                }
            )
            self.stdout.write("Created Academic Period")

            # 11. Create Survey Templates
            SurveyTemplate.objects.get_or_create(
                name="Default Exit Survey", type="exit", is_default=True,
                defaults={"definition": {}}
            )
            SurveyTemplate.objects.get_or_create(
                name="90-Day Follow-Up", type="follow_up",
                defaults={"definition": {}}
            )
            self.stdout.write("Created Survey Templates")

        self.stdout.write(self.style.SUCCESS("Database seeded successfully!"))