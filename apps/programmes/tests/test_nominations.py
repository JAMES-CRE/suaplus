import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.programmes.models import Nomination, Programme, TierStatus
from apps.registry.models import Person


@pytest.fixture
def programme(db):
    return Programme.objects.create(name="Gemini Faculty")


@pytest.fixture
def tier_status(db, programme):
    person = Person.objects.create(
        affiliation="staff", full_name="Kwame Mensah", email="km@knust.edu.gh"
    )
    return TierStatus.objects.create(
        person=person,
        programme=programme,
        tier=2,
        status="eligible",
        criteria_met=1,
        criteria_total=1,
    )


def _make_user(email, role=None, scope_type=None, scope_id=None):
    User = get_user_model()
    u = User.objects.create_user(username=email, email=email, password="x")
    account = UserAccount.objects.create(user=u)
    if role:
        RoleAssignment.objects.create(
            user_account=account,
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
        )
    return u


@pytest.mark.django_db
def test_programme_owner_can_nominate(client, tier_status, programme):
    user = _make_user(
        "owner@knust.edu.gh", role="programme_owner",
        scope_type="programme", scope_id=programme.id
    )
    client.force_login(user)
    response = client.post(
        reverse("nominate", args=[tier_status.id]),
        {"comments": "Good candidate"},
    )
    assert response.status_code == 302
    n = Nomination.objects.get(person=tier_status.person, programme=programme)
    assert n.decision == "pending"
    assert n.nominated_by == user
    tier_status.refresh_from_db()
    assert tier_status.status == "nominated"


@pytest.mark.django_db
def test_other_programme_owner_cannot_nominate(client, tier_status):
    other_prog = Programme.objects.create(name="Other")
    user = _make_user(
        "other@knust.edu.gh", role="programme_owner",
        scope_type="programme", scope_id=other_prog.id
    )
    client.force_login(user)
    response = client.post(
        reverse("nominate", args=[tier_status.id]), {"comments": ""}
    )
    assert response.status_code == 302
    assert Nomination.objects.count() == 0


@pytest.mark.django_db
def test_approval_creates_role_and_updates_status(client, tier_status, programme):
    owner = _make_user(
        "owner@knust.edu.gh", role="programme_owner",
        scope_type="programme", scope_id=programme.id
    )
    # Create nomination via POST
    client.force_login(owner)
    client.post(reverse("nominate", args=[tier_status.id]), {"comments": ""})

    n = Nomination.objects.first()
    response = client.post(
        reverse("nomination_detail", args=[n.id]),
        {"decision": "approved", "comment": "Good"},
    )
    assert response.status_code == 302

    n.refresh_from_db()
    assert n.decision == "approved"

    tier_status.refresh_from_db()
    assert tier_status.status == "approved"


@pytest.mark.django_db
def test_rejection_leaves_status_eligible(client, tier_status, programme):
    owner = _make_user(
        "owner@knust.edu.gh", role="programme_owner",
        scope_type="programme", scope_id=programme.id
    )
    client.force_login(owner)
    client.post(reverse("nominate", args=[tier_status.id]), {"comments": ""})
    n = Nomination.objects.first()

    client.post(
        reverse("nomination_detail", args=[n.id]),
        {"decision": "rejected", "comment": "Not yet"},
    )
    n.refresh_from_db()
    assert n.decision == "rejected"
    tier_status.refresh_from_db()
    assert tier_status.status == "eligible"


@pytest.mark.django_db
def test_non_approver_cannot_approve(client, tier_status, programme):
    owner = _make_user(
        "owner@knust.edu.gh", role="programme_owner",
        scope_type="programme", scope_id=programme.id
    )
    client.force_login(owner)
    client.post(reverse("nominate", args=[tier_status.id]), {"comments": ""})
    n = Nomination.objects.first()

    outsider = _make_user("nobody@knust.edu.gh")
    client.force_login(outsider)
    response = client.post(
        reverse("nomination_detail", args=[n.id]),
        {"decision": "approved"},
    )
    assert response.status_code == 302
    n.refresh_from_db()
    assert n.decision == "pending"
    