import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.accounts.models import RoleAssignment, UserAccount
from apps.programmes.models import Programme, TierStatus
from apps.registry.models import OrgUnit, Person


@pytest.fixture
def college(db):
    return OrgUnit.objects.create(name="Engineering", type="college")


@pytest.fixture
def programme(db):
    return Programme.objects.create(name="Gemini Faculty")


@pytest.fixture
def tier_status(db, programme, college):
    person = Person.objects.create(
        affiliation="staff", full_name="Kwame Mensah", unit=college
    )
    return TierStatus.objects.create(
        person=person,
        programme=programme,
        tier=2,
        status="eligible",
        criteria_met=3,
        criteria_total=3,
    )


def _make_hub_user(email, role=None, scope_type=None, scope_id=None):
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
def test_scout_requires_login(client):
    response = client.get(reverse("scout_list"))
    assert response.status_code == 302
    assert "/admin/login/" in response.url or "/accounts/login/" in response.url


@pytest.mark.django_db
def test_super_admin_sees_all(client, tier_status):
    user = _make_hub_user("admin@knust.edu.gh", role="super_admin", scope_type="all")
    client.force_login(user)
    response = client.get(reverse("scout_list"))
    assert response.status_code == 200
    assert b"Kwame Mensah" in response.content


@pytest.mark.django_db
def test_programme_owner_sees_only_own(client, tier_status, programme):
    other = Programme.objects.create(name="Other Prog")
    other_person = Person.objects.create(affiliation="staff", full_name="Other Person")
    TierStatus.objects.create(
        person=other_person,
        programme=other,
        tier=2,
        status="eligible",
        criteria_met=1,
        criteria_total=1,
    )
    user = _make_hub_user(
        "owner@knust.edu.gh",
        role="programme_owner",
        scope_type="programme",
        scope_id=programme.id,
    )
    client.force_login(user)
    response = client.get(reverse("scout_list"))
    assert response.status_code == 200
    assert b"Kwame Mensah" in response.content
    assert b"Other Person" not in response.content


@pytest.mark.django_db
def test_user_with_no_role_sees_nothing(client, tier_status):
    user = _make_hub_user("nobody@knust.edu.gh")
    client.force_login(user)
    response = client.get(reverse("scout_list"))
    assert response.status_code == 200
    assert b"Kwame Mensah" not in response.content
    assert b"No eligible people" in response.content