import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.registry.models import OrgUnit
from apps.core.models import AcademicPeriod

class Command(BaseCommand):
    help = "Seeds the KNUST OrgUnit hierarchy (Colleges, Faculties, Departments)"

    def handle(self, *args, **options):
        self.stdout.write("Starting KNUST Unit Seed...")

        with transaction.atomic():
            # 1. Create the Campus (Root)
            campus, _ = OrgUnit.objects.get_or_create(
                name="KNUST Campus", defaults={"type": "campus", "short_name": "KNUST"}
            )

            # 2. Define the Colleges
            colleges_data = [
                ("College of Agriculture and Natural Resources", "CANR"),
                ("College of Art and Built Environment", "CABE"),
                ("College of Humanities and Social Sciences", "CHSS"),
                ("College of Engineering", "COE"),
                ("College of Health Sciences", "CHS"),
                ("College of Science", "COS"),
            ]

            colleges = {}
            for name, short_name in colleges_data:
                college, _ = OrgUnit.objects.get_or_create(
                    name=name,
                    defaults={"type": "college", "short_name": short_name, "parent": campus}
                )
                colleges[short_name] = college

            # 3. Define Faculties and Departments
            # (A simplified but accurate breakdown)
            structure = {
                "COE": {
                    "Faculty of Civil and Geo-Engineering": [
                        "Department of Civil Engineering",
                        "Department of Geomatic Engineering",
                    ],
                    "Faculty of Mechanical and Chemical Engineering": [
                        "Department of Mechanical Engineering",
                        "Department of Chemical Engineering",
                        "Department of Petroleum Engineering",
                    ],
                    "Faculty of Electrical and Computer Engineering": [
                        "Department of Computer Engineering",
                        "Department of Electrical Engineering",
                        "Department of Telecommunication Engineering",
                    ],
                    "Faculty of Materials Science and Engineering": [
                        "Department of Materials Engineering",
                        "Department of Biomedical Engineering",
                    ],
                },
                "COS": {
                    "Faculty of Physical and Computational Sciences": [
                        "Department of Computer Science",
                        "Department of Mathematics",
                        "Department of Physics",
                        "Department of Chemistry",
                    ],
                    "Faculty of Biosciences": [
                        "Department of Biochemistry and Biotechnology",
                        "Department of Food Science",
                        "Department of Environmental Science",
                    ],
                },
                "CHS": {
                    "School of Medicine and Dentistry": [
                        "Department of Medicine",
                        "Department of Dentistry",
                    ],
                    "School of Pharmacy and Pharmaceutical Sciences": [
                        "Department of Pharmacy",
                        "Department of Pharmaceutical Chemistry",
                    ],
                    "Faculty of Allied Health Sciences": [
                        "Department of Nursing",
                        "Department of Medical Laboratory Science",
                    ],
                },
                "CABE": {
                    "Faculty of Built Environment": [
                        "Department of Architecture",
                        "Department of Construction Technology",
                        "Department of Land Economy",
                    ],
                    "Faculty of Art": [
                        "Department of Fine Art",
                        "Department of Industrial Art",
                    ],
                },
                "CHSS": {
                    "Faculty of Social Sciences": [
                        "Department of Economics",
                        "Department of Geography",
                        "Department of Sociology",
                        "Department of Political Science",
                    ],
                    "Faculty of Law": [
                        "Department of Law",
                    ],
                    "Faculty of Humanities": [
                        "Department of English",
                        "Department of Languages",
                    ],
                },
                "CANR": {
                    "Faculty of Agriculture": [
                        "Department of Crop and Soil Sciences",
                        "Department of Animal Science",
                        "Department of Agricultural Economics",
                    ],
                    "Faculty of Renewable Natural Resources": [
                        "Department of Agroforestry",
                        "Department of Fisheries and Watershed Management",
                    ],
                },
            }

            for college_short, faculties in structure.items():
                college = colleges[college_short]
                for faculty_name, departments in faculties.items():
                    faculty, _ = OrgUnit.objects.get_or_create(
                        name=faculty_name,
                        defaults={"type": "faculty", "parent": college}
                    )
                    for dept_name in departments:
                        OrgUnit.objects.get_or_create(
                            name=dept_name,
                            defaults={"type": "department", "parent": faculty}
                        )

        self.stdout.write(self.style.SUCCESS("KNUST OrgUnit hierarchy seeded successfully."))