from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.context_processors import navigation
from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.views import hub_home
from apps.passport.models import Credential
from apps.programmes.models import Programme
from apps.registry.models import OrgUnit, Person
from apps.sessions.models import Facilitation, Session


def request_for(user):
    request = RequestFactory().get("/hub/")
    request.user = user
    return request


def test_anonymous_navigation_has_no_staff_tools():
    assert not any(navigation(request_for(AnonymousUser()))["sua_nav"].values())


@pytest.mark.django_db
def test_participant_navigation_has_no_staff_tools():
    user = get_user_model().objects.create_user(username="participant")
    account = UserAccount.objects.create(user=user)
    RoleAssignment.objects.create(
        user_account=account, role="participant", scope_type="own"
    )
    assert not any(navigation(request_for(user))["sua_nav"].values())


@pytest.mark.django_db
def test_hub_next_workshop_stays_within_facilitator_scope():
    user = get_user_model().objects.create_user(username="facilitator")
    account = UserAccount.objects.create(user=user)
    assigned = Programme.objects.create(name="Assigned programme")
    other = Programme.objects.create(name="Other programme")
    RoleAssignment.objects.create(
        user_account=account,
        role="facilitator",
        scope_type="programme",
        scope_id=assigned.id,
    )
    for programme, title, hours in [
        (other, "Hidden earlier workshop", 1),
        (assigned, "My next workshop", 2),
    ]:
        session = Session.objects.create(
            programme=programme,
            title=title,
            mode="in_person",
            start_at=timezone.now() + timedelta(hours=hours),
            end_at=timezone.now() + timedelta(hours=hours + 1),
        )
        if programme == assigned:
            trainer = Person.objects.create(full_name="Trainer", affiliation="staff")
            account.person = trainer
            account.save(update_fields=["person"])
            Facilitation.objects.create(person=trainer, session=session, role="lead")
    content = hub_home(request_for(user)).content.decode()
    assert "My next workshop" in content
    assert "Hidden earlier workshop" not in content
    assert 'aria-label="Main navigation"' in content


@pytest.mark.django_db
def test_hub_review_count_stays_within_focal_person_scope():
    user = get_user_model().objects.create_user(username="reviewer")
    account = UserAccount.objects.create(user=user)
    own_unit = OrgUnit.objects.create(name="Own unit", type="college")
    other_unit = OrgUnit.objects.create(name="Other unit", type="college")
    RoleAssignment.objects.create(
        user_account=account,
        role="focal_person",
        scope_type="unit",
        scope_id=own_unit.id,
    )
    for number, unit in enumerate([own_unit, other_unit, other_unit]):
        person = Person.objects.create(
            full_name=f"Person {number}", affiliation="staff", unit=unit
        )
        Credential.objects.create(
            person=person,
            kind="external",
            title="Pending certificate",
            issued_at=timezone.localdate(),
        )
    content = hub_home(request_for(user)).content.decode()
    assert "1 credential awaiting review" in content
    assert "3 credentials awaiting review" not in content
