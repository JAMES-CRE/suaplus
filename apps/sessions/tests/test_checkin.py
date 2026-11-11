import datetime as dt

import pytest
from django.urls import reverse

from apps.library.models import Competency, Domain, Framework, Level
from apps.programmes.models import Programme
from apps.registry.models import Person
from apps.sessions.models import Attendance, Session


@pytest.fixture
def framework(db):
    f = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    Level.objects.create(framework=f, ordinal=1, label="Aware")
    return f


@pytest.fixture
def domain(framework):
    return Domain.objects.create(framework=framework, code="AI", name="AI")


@pytest.fixture
def competency(domain):
    return Competency.objects.create(
        framework=domain.framework, domain=domain, code="AI-01", title="AI basics"
    )


@pytest.fixture
def programme(db):
    return Programme.objects.create(name="Test Programme")


@pytest.fixture
def session(programme, competency):
    from apps.sessions.models import SessionCompetency

    s = Session.objects.create(
        programme=programme,
        title="Test Session",
        start_at=dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 11, 1, 17, 0, tzinfo=dt.timezone.utc),
        mode="in_person",
        venue="Room A",
    )
    SessionCompetency.objects.create(session=s, competency=competency)
    return s


@pytest.mark.django_db
def test_checkin_form_renders(client, session):
    response = client.get(reverse("checkin", args=[session.session_code]))
    assert response.status_code == 200
    assert b"Check in" in response.content


@pytest.mark.django_db
def test_checkin_creates_attendance(client, session, competency):
    response = client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "staff",
            "full_name": "Kwame Mensah",
            "staff_or_student_no": "STF001",
            "email": "km@knust.edu.gh",
            "phone": "",
            "organisation_name": "",
            "consent": "on",
        },
        follow=True,
    )
    assert response.status_code == 200
    person = Person.objects.get(staff_or_student_no="STF001")
    attendance = Attendance.objects.get(session=session, person=person)
    assert attendance.status == "partial"
    assert attendance.method == "code"


@pytest.mark.django_db
def test_checkin_matches_existing_person(client, session):
    Person.objects.create(
        affiliation="staff",
        staff_or_student_no="STF001",
        full_name="Kwame Mensah",
        email="km@knust.edu.gh",
    )
    client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "staff",
            "full_name": "Kwame Mensah",
            "staff_or_student_no": "STF001",
            "email": "km@knust.edu.gh",
            "phone": "",
            "organisation_name": "",
            "consent": "on",
        },
    )
    assert Person.objects.filter(staff_or_student_no="STF001").count() == 1


@pytest.mark.django_db
def test_checkin_requires_consent(client, session):
    response = client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "staff",
            "full_name": "Test Person",
            "staff_or_student_no": "STF002",
            "email": "",
            "phone": "",
            "organisation_name": "",
            # consent missing
        },
    )
    assert response.status_code == 200  # returns form with error
    assert Attendance.objects.count() == 0


@pytest.mark.django_db
def test_checkin_creates_evidence(client, session, competency):
    from apps.passport.models import Evidence

    client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "staff",
            "full_name": "Kwame Mensah",
            "staff_or_student_no": "STF001",
            "email": "km@knust.edu.gh",
            "phone": "",
            "organisation_name": "",
            "consent": "on",
        },
    )
    assert Evidence.objects.filter(
        competency=competency, type="attendance"
    ).count() == 1