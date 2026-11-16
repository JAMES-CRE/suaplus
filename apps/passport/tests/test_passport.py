import hashlib
import secrets
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from unittest.mock import patch

from apps.accounts.models import RoleAssignment, UserAccount
from apps.library.models import (
    CertificationCatalogue,
    CertificationCompetency,
    Competency,
    Domain,
    Framework,
    Level,
)
from apps.passport.models import Credential, Evidence, LearningGoal
from apps.passport.forms import CredentialSubmitForm
from apps.passport.linking import issue_passport_link
from apps.passport.services import expire_due_records
from apps.passport.views import _issue_badge_for_credential
from apps.programmes.models import Programme
from apps.registry.models import PassportToken
from apps.registry.models import Person
from apps.registry.models import OrgUnit


@pytest.fixture
def person(db):
    return Person.objects.create(
        affiliation="staff",
        full_name="Kwame Mensah",
        email="km@knust.edu.gh",
        phone="+233244123456",
    )


@pytest.fixture
def passport_token(person):
    token = secrets.token_urlsafe(32)
    PassportToken.objects.create(
        person=person,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
    )
    return token


@pytest.mark.django_db
def test_passport_renders(client, passport_token, person):
    response = client.get(reverse("passport", args=[passport_token]))
    assert response.status_code == 200
    assert b"Kwame Mensah" in response.content


@pytest.mark.django_db
def test_invalid_token_returns_404(client):
    response = client.get(reverse("passport", args=["fake-token"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_revoked_token_returns_404(client, passport_token, person):
    from django.utils import timezone

    pt = PassportToken.objects.get(person=person)
    pt.revoked_at = timezone.now()
    pt.save()

    response = client.get(reverse("passport", args=[passport_token]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_issuing_a_fresh_link_revokes_the_previous_link(client, person):
    old_url = issue_passport_link(person)
    new_url = issue_passport_link(person)
    old_token = old_url.rstrip("/").split("/")[-1]
    new_token = new_url.rstrip("/").split("/")[-1]

    assert PassportToken.objects.filter(person=person, revoked_at__isnull=True).count() == 1
    assert client.get(reverse("passport", args=[old_token])).status_code == 404
    assert client.get(reverse("passport", args=[new_token])).status_code == 200


@pytest.mark.django_db
def test_passport_holder_can_add_and_complete_a_learning_goal(
    client, passport_token, person
):
    response = client.post(
        reverse("passport_goals", args=[passport_token]),
        {"action": "add", "title": "Complete the data pathway", "target_date": "2026-12-01"},
    )

    assert response.status_code == 302
    goal = LearningGoal.objects.get(person=person)
    assert goal.title == "Complete the data pathway"
    assert str(goal.target_date) == "2026-12-01"
    assert goal.status == "active"

    response = client.post(
        reverse("passport_goals", args=[passport_token]),
        {"action": "complete", "goal_id": goal.id},
    )

    assert response.status_code == 302
    goal.refresh_from_db()
    assert goal.status == "completed"
    assert goal.completed_at is not None


@pytest.mark.django_db
def test_badge_is_issued_once_for_an_approved_catalogue_credential(person):
    framework = Framework.objects.create(code="BADGE", name="Badge framework", is_knust=True)
    level = Level.objects.create(framework=framework, ordinal=2, label="Developing")
    domain = Domain.objects.create(framework=framework, code="DG", name="Digital")
    competency = Competency.objects.create(
        framework=framework, domain=domain, code="DG-01", title="Digital basics"
    )
    catalogue = CertificationCatalogue.objects.create(
        issuer="Example Issuer", title="Example Certificate", active=True
    )
    CertificationCompetency.objects.create(
        certification=catalogue, competency=competency, max_level=level.ordinal
    )
    credential = Credential.objects.create(
        person=person,
        catalogue=catalogue,
        kind="external",
        source="self_submitted",
        issuer=catalogue.issuer,
        title=catalogue.title,
        issued_at=date(2026, 1, 1),
        review_status="approved",
    )

    with patch("apps.passport.views.generate_badge_json", return_value="signed-badge") as generate:
        assert _issue_badge_for_credential(credential) is True
        assert _issue_badge_for_credential(credential) is False

    credential.refresh_from_db()
    assert credential.badge_json == {"jwt": "signed-badge"}
    generate.assert_called_once_with(person, competency, level)


@pytest.mark.django_db
def test_expiry_sweep_expires_due_credentials_and_evidence(person):
    framework = Framework.objects.create(code="EXPIRY", name="Expiry framework", is_knust=True)
    level = Level.objects.create(framework=framework, ordinal=1, label="Aware")
    domain = Domain.objects.create(framework=framework, code="EX", name="Expiry")
    competency = Competency.objects.create(
        framework=framework, domain=domain, code="EX-01", title="Expiry basics"
    )
    expired_credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        title="Expired certificate",
        issued_at=date(2025, 1, 1),
        expires_at=date(2026, 1, 1),
        status="valid",
    )
    active_credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        title="Active certificate",
        issued_at=date(2025, 1, 1),
        expires_at=date(2026, 12, 1),
        status="valid",
    )
    evidence = Evidence.objects.create(
        person=person,
        competency=competency,
        level=level,
        type="attendance",
        source_type="",
        expires_at=date(2026, 1, 1),
    )

    result = expire_due_records(today=date(2026, 1, 2))

    expired_credential.refresh_from_db()
    active_credential.refresh_from_db()
    evidence.refresh_from_db()
    assert result == {"credentials": 1, "evidence": 1}
    assert expired_credential.status == "expired"
    assert active_credential.status == "valid"
    assert evidence.status == "expired"
    assert evidence.is_expired is True


@pytest.mark.django_db
def test_credential_submission_requires_reference_and_valid_certificate_file():
    data = {
        "title": "Example Certificate",
        "issuer": "Example Issuer",
        "issued_at": "2026-01-01",
    }

    missing_reference = CredentialSubmitForm(
        data=data,
        files={"file": SimpleUploadedFile("certificate.pdf", b"%PDF-1.4\n")},
    )
    assert missing_reference.is_valid() is False
    assert "credential ID or a verification URL" in missing_reference.non_field_errors()[0]

    missing_file = CredentialSubmitForm(
        data={**data, "credential_id": "CERT-123"}
    )
    assert missing_file.is_valid() is False
    assert "file" in missing_file.errors

    oversized_file = CredentialSubmitForm(
        data={**data, "credential_id": "CERT-123"},
        files={
            "file": SimpleUploadedFile(
                "certificate.pdf", b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024)
            )
        },
    )
    assert oversized_file.is_valid() is False
    assert "5 MB or smaller" in oversized_file.errors["file"][0]

    valid_submission = CredentialSubmitForm(
        data={**data, "credential_id": "CERT-123"},
        files={"file": SimpleUploadedFile("certificate.pdf", b"%PDF-1.4\n")},
    )
    assert valid_submission.is_valid() is True


@pytest.mark.django_db
def test_email_masked(client, passport_token):
    response = client.get(reverse("passport", args=[passport_token]))
    content = response.content.decode()
    assert "km@knust.edu.gh" not in content
    assert "k•@knust.edu.gh" in content


@pytest.mark.django_db
def test_passport_transcript_pdf_downloads(client, passport_token):
    response = client.get(reverse("passport_transcript_pdf", args=[passport_token]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_reviewer_cannot_approve_own_credential_submission(client):
    person = Person.objects.create(
        affiliation="staff",
        full_name="Self Reviewer",
        email="reviewer@knust.edu.gh",
    )
    user = get_user_model().objects.create_user(
        username="reviewer@knust.edu.gh",
        email="reviewer@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user, person=person)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        issuer="Example Issuer",
        title="Example Certificate",
        issued_at=date(2026, 1, 1),
        verification_url="https://example.org/verify",
    )
    client.force_login(user)

    response = client.post(
        reverse("credential_review_detail", args=[credential.id]),
        {"decision": "approved", "comment": "Looks valid"},
    )

    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.review_status == "submitted"
    assert credential.reviewer is None
    assert credential.reviews.count() == 0


@pytest.mark.django_db
def test_reviewer_cannot_open_credential_outside_assigned_scope(client):
    allowed_unit = OrgUnit.objects.create(name="Allowed Unit", type="college")
    other_unit = OrgUnit.objects.create(name="Other Unit", type="college")
    user = get_user_model().objects.create_user(
        username="focal@knust.edu.gh",
        email="focal@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account,
        role="focal_person",
        scope_type="unit",
        scope_id=allowed_unit.id,
    )
    person = Person.objects.create(
        affiliation="staff", full_name="Other Person", unit=other_unit
    )
    credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        issuer="Example Issuer",
        title="Example Certificate",
        issued_at=date(2026, 1, 1),
        verification_url="https://example.org/verify",
    )
    client.force_login(user)

    response = client.get(
        reverse("credential_review_detail", args=[credential.id])
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_resend_limit_is_per_email_and_response_does_not_reveal_account(client):
    cache.clear()
    person = Person.objects.create(
        affiliation="staff",
        full_name="Passport Owner",
        email="owner@knust.edu.gh",
    )
    with patch("apps.passport.views.send_notification") as send_notification:
        responses = [
            client.post(
                reverse("resend_link"), {"email": "owner@knust.edu.gh"}
            )
            for _ in range(4)
        ]

    generic_message = "If that email is in our system, a new passport link has been sent to it."
    assert all(response.status_code == 200 for response in responses)
    assert all(generic_message in response.content.decode() for response in responses)
    assert send_notification.call_count == 3
    assert person.passport_tokens.count() == 3
    assert person.passport_tokens.filter(revoked_at__isnull=True).count() == 1

    unknown_response = client.post(
        reverse("resend_link"), {"email": "unknown@knust.edu.gh"}
    )
    assert unknown_response.status_code == 200
    assert generic_message in unknown_response.content.decode()
    assert "unknown@knust.edu.gh" not in unknown_response.content.decode()


@pytest.mark.django_db
def test_resend_ip_limit_allows_ten_requests_per_hour(client):
    cache.clear()
    url = reverse("resend_link")

    responses = [
        client.post(url, {"email": f"person{index}@knust.edu.gh"})
        for index in range(11)
    ]

    assert all(response.status_code == 200 for response in responses[:10])
    assert responses[10].status_code == 429


@pytest.mark.django_db
def test_correction_request_is_routed_to_selected_session_facilitators(client, passport_token, person):
    from datetime import datetime, timezone as dt_timezone
    from apps.sessions.models import Attendance, Facilitation, Session

    programme = Programme.objects.create(name="Passport correction programme")
    session = Session.objects.create(
        programme=programme,
        title="Passport correction workshop",
        start_at=datetime(2026, 10, 1, 9, tzinfo=dt_timezone.utc),
        end_at=datetime(2026, 10, 1, 10, tzinfo=dt_timezone.utc),
        mode="in_person",
    )
    Attendance.objects.create(
        person=person,
        session=session,
        method="manual",
        status="completed",
        check_in_at=session.start_at,
        check_out_at=session.end_at,
    )
    facilitator = Person.objects.create(
        affiliation="staff", full_name="Facilitator", email="facilitator@knust.edu.gh"
    )
    Facilitation.objects.create(session=session, person=facilitator, role="lead")

    with patch("apps.passport.views.send_correction_request_to_facilitators") as notify:
        response = client.post(
            reverse("report_error", args=[passport_token]),
            {"session": session.id, "message": "My attendance date is incorrect."},
        )

    assert response.status_code == 200
    request = person.correction_requests.get()
    assert request.session == session
    notify.assert_called_once_with(request)


@pytest.mark.django_db
def test_credential_can_be_returned_for_information_with_comment(client):
    person = Person.objects.create(
        affiliation="staff", full_name="Credential Owner", email="owner@example.org"
    )
    user = get_user_model().objects.create_user(
        username="reviewer2@knust.edu.gh",
        email="reviewer2@knust.edu.gh",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        issuer="Example Issuer",
        title="Example Certificate",
        issued_at=date(2026, 1, 1),
        verification_url="https://example.org/verify",
    )
    client.force_login(user)
    url = reverse("credential_review_detail", args=[credential.id])

    client.post(url, {"decision": "returned", "comment": ""})
    credential.refresh_from_db()
    assert credential.review_status == "submitted"

    with patch("apps.passport.views.send_notification") as send_notification:
        response = client.post(
            url,
            {"decision": "returned", "comment": "Please attach the certificate."},
        )

    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.review_status == "returned"
    assert credential.review_note == "Please attach the certificate."
    assert credential.reviews.get().to_status == "returned"
    assert credential.evidence.count() == 0
    send_notification.assert_called_once()
    assert send_notification.call_args.kwargs["template_key"] == "credential_returned"


@pytest.mark.django_db
def test_reviewer_can_mark_credential_in_review_and_notify_participant(client):
    person = Person.objects.create(
        affiliation="staff", full_name="Credential Owner", email="owner@example.org"
    )
    user = get_user_model().objects.create_user(
        username="reviewer@example.org",
        email="reviewer@example.org",
        password="test-password",
    )
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        issuer="Example Issuer",
        title="Example Certificate",
        issued_at=date(2026, 1, 1),
        verification_url="https://example.org/verify",
    )
    client.force_login(user)

    with patch("apps.passport.views.send_notification") as send_notification:
        response = client.post(
            reverse("credential_review_detail", args=[credential.id]),
            {"decision": "in_review", "comment": ""},
        )

    assert response.status_code == 302
    credential.refresh_from_db()
    assert credential.review_status == "in_review"
    assert credential.reviewer == user
    assert credential.reviews.get().to_status == "in_review"
    assert send_notification.call_args.kwargs["template_key"] == "credential_in_review"


@pytest.mark.django_db
def test_owner_can_update_and_resubmit_returned_credential(client):
    unit = OrgUnit.objects.create(name="Owner Unit", type="department")
    person = Person.objects.create(
        affiliation="staff",
        full_name="Credential Owner",
        email="owner@example.org",
        title="Lecturer",
        category="academic",
        unit=unit,
    )
    user = get_user_model().objects.create_user(
        username="owner@example.org",
        email="owner@example.org",
        password="test-password",
    )
    UserAccount.objects.create(user=user, person=person)
    credential = Credential.objects.create(
        person=person,
        kind="external",
        source="self_submitted",
        issuer="Example Issuer",
        title="Example Certificate",
        issued_at=date(2026, 1, 1),
        verification_url="https://example.org/verify",
        review_status="returned",
        review_note="Please attach the certificate.",
    )
    client.force_login(user)
    url = f"{reverse('credential_submit')}?resubmit={credential.id}"

    page = client.get(url)
    assert page.status_code == 200
    assert b"Please attach the certificate." in page.content
    assert b"Resubmit Credential" in page.content

    response = client.post(
        url,
        {
            "title": "Updated Certificate",
            "issuer": "Example Issuer",
            "credential_id": "CERT-123",
            "issued_at": "2026-01-01",
            "expires_at": "",
            "verification_url": "https://example.org/verify-updated",
            "other_description": "Updated proof details",
            "file": SimpleUploadedFile("updated-certificate.pdf", b"%PDF-1.4\n"),
        },
    )

    assert response.status_code == 200
    credential.refresh_from_db()
    assert credential.title == "Updated Certificate"
    assert credential.review_status == "submitted"
    assert credential.review_note == ""
    assert credential.reviewer is None
    assert credential.reviewed_at is None
    resubmission = credential.reviews.get(to_status="submitted")
    assert resubmission.from_status == "returned"
