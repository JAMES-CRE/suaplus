import datetime as dt

import pytest
from django.core.cache import cache
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
def test_checkout_console_uses_the_canonical_identify_url(client, session):
    from django.contrib.auth import get_user_model

    from apps.accounts.models import RoleAssignment, UserAccount

    user = get_user_model().objects.create_user(
        username="admin@example.org", password="test-password"
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    client.force_login(user)

    response = client.get(reverse("session_checkout_console", args=[session.id]))

    expected_url = "http://testserver" + reverse(
        "checkout_identify", kwargs={"code": session.session_code}
    )
    assert response.status_code == 200
    assert expected_url.encode() in response.content
    assert b"data:image/svg+xml;base64," in response.content


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
            "age_band": "18_plus",
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
        age_band="18_plus",
    )
    client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "staff",
            "full_name": "Kwame Mensah",
            "staff_or_student_no": "STF001",
            "email": "km@knust.edu.gh",
            "phone": "",
            "age_band": "18_plus",
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
            "age_band": "18_plus",
            "organisation_name": "",
            "consent": "on",
        },
    )
    evidence = Evidence.objects.get(competency=competency, type="attendance")
    assert evidence.framework_version.startswith(f"{competency.framework.code}:")
    assert evidence.framework_version != "v1"


@pytest.mark.django_db
def test_under_18_checkin_records_guardian_consent(client, session):
    from apps.registry.models import Consent, Organisation

    response = client.post(
        reverse("checkin", args=[session.session_code]),
        {
            "affiliation": "external",
            "full_name": "Student Example",
            "email": "student@example.org",
            "phone": "",
            "organisation_name": "Example School",
            "age_band": "under_18",
            "guardian_name": "Guardian Example",
            "guardian_contact": "guardian@example.org",
            "guardian_consent": "on",
        },
        follow=True,
    )

    assert response.status_code == 200
    person = Person.objects.get(email="student@example.org")
    assert person.age_band == "under_18"
    assert person.phone is None
    assert person.organisation.name == "Example School"
    assert Consent.objects.get(person=person, type="data_processing").given_by == "Guardian Example"
    assert Consent.objects.get(person=person, type="guardian").given_by == "Guardian Example"
    assert Consent.objects.get(person=person, type="guardian").contact_details == "guardian@example.org"


@pytest.mark.django_db
def test_under_18_checkout_sends_passport_link_to_partner_coordinator(
    client, session
):
    from django.contrib.auth import get_user_model
    from django.core import mail
    from django.test import override_settings

    from apps.accounts.models import RoleAssignment, UserAccount
    from apps.registry.models import Organisation

    organisation = Organisation.objects.create(
        name="Example School", type="senior_high_school"
    )
    minor = Person.objects.create(
        affiliation="external",
        full_name="Student Example",
        email="student@example.org",
        age_band="under_18",
        organisation=organisation,
    )
    attendance = Attendance.objects.create(
        session=session,
        person=minor,
        method="qr",
        check_in_at=session.start_at,
        status="partial",
    )
    user = get_user_model().objects.create_user(
        username="coordinator@example.org",
        email="coordinator@example.org",
        password="test-password",
    )
    coordinator = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=coordinator,
        role="partner_coordinator",
        scope_type="organisation",
        scope_id=organisation.id,
    )

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Sua+ <noreply@example.org>",
    ):
        response = client.post(
            reverse("checkout", args=[session.session_code, minor.id]),
            {"csat": 5},
        )

    assert response.status_code == 200
    assert "sent to the Partner Coordinator" in response.content.decode()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["coordinator@example.org"]
    assert "student@example.org" not in mail.outbox[0].to
    attendance.refresh_from_db()
    assert attendance.status == "completed"


@pytest.mark.django_db
def test_under_18_checkout_sends_passport_link_to_guardian_email(client, session):
    from django.core import mail
    from django.test import override_settings

    from apps.registry.models import Consent, Organisation

    organisation = Organisation.objects.create(
        name="Guardian Example School", type="senior_high_school"
    )
    minor = Person.objects.create(
        affiliation="external",
        full_name="Student Example",
        email="student@example.org",
        age_band="under_18",
        organisation=organisation,
    )
    Consent.objects.create(
        person=minor,
        type="guardian",
        given_by="Parent Example",
        contact_details="parent@example.org",
        notice_version="1.0",
    )
    Attendance.objects.create(
        session=session,
        person=minor,
        method="qr",
        check_in_at=session.start_at,
        status="partial",
    )

    with override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Sua+ <noreply@example.org>",
    ):
        response = client.post(
            reverse("checkout", args=[session.session_code, minor.id]),
            {"csat": 5},
        )

    assert response.status_code == 200
    assert "sent to the parent or guardian contact" in response.content.decode()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["parent@example.org"]
    assert "student@example.org" not in mail.outbox[0].to
    assert "/p/" in mail.outbox[0].body


@pytest.mark.django_db
def test_closing_session_keeps_unchecked_out_attendance_partial(client, session):
    from django.contrib.auth import get_user_model

    from apps.accounts.models import RoleAssignment, UserAccount

    person = Person.objects.create(
        affiliation="staff",
        full_name="Partial Attendee",
        email="partial@example.org",
    )
    attendance = Attendance.objects.create(
        session=session,
        person=person,
        method="import",
        check_in_at=session.start_at,
        status="partial",
    )
    user = get_user_model().objects.create_user(
        username="session-admin@knust.edu.gh",
        email="session-admin@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    client.force_login(user)

    response = client.post(reverse("session_close", args=[session.id]))

    assert response.status_code == 302
    session.refresh_from_db()
    attendance.refresh_from_db()
    assert session.status == "closed"
    assert attendance.status == "partial"
    assert attendance.check_out_at is None


@pytest.mark.django_db
def test_shared_ip_can_load_checkin_for_a_large_workshop(client, session):
    cache.clear()

    responses = [
        client.get(reverse("checkin", args=[session.session_code]))
        for _ in range(25)
    ]

    assert all(response.status_code == 200 for response in responses)
