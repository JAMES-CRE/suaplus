import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.permissions import (
    get_user_scopes,
    is_focal_person_for,
    is_super_admin,
    scope_queryset,
)
from apps.registry.models import OrgUnit, Person


@pytest.fixture
def college(db):
    return OrgUnit.objects.create(name="College of Engineering", type="college")


@pytest.fixture
def person(db):
    return Person.objects.create(
        affiliation="staff", full_name="Kwame Mensah", email="km@knust.edu.gh"
    )


@pytest.fixture
def user(db, person):
    User = get_user_model()
    u = User.objects.create_user(
        username="km@knust.edu.gh",
        email="km@knust.edu.gh",
        password="x",
    )
    UserAccount.objects.create(user=u, person=person)
    return u


@pytest.mark.django_db
def test_no_scopes_returns_empty(user):
    assert get_user_scopes(user) == []


@pytest.mark.django_db
def test_super_admin_bypasses_scoping(user):
    account = user.hub_account
    RoleAssignment.objects.create(
        user_account=account, role="super_admin", scope_type="all"
    )
    assert is_super_admin(user)


@pytest.mark.django_db
def test_focal_person_scoped_to_unit(user, college):
    account = user.hub_account
    RoleAssignment.objects.create(
        user_account=account,
        role="focal_person",
        scope_type="unit",
        scope_id=college.id,
    )
    assert is_focal_person_for(user, college.id)
    assert not is_focal_person_for(user, college.id + 1)


@pytest.mark.django_db
def test_scope_queryset_returns_nothing_for_unscoped_user(user, person):
    qs = Person.objects.all()
    filtered = scope_queryset(qs, user, {"unit": "unit_id"})
    assert filtered.count() == 0