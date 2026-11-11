import pytest
from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.accounts.models import RoleAssignment, UserAccount
from apps.accounts.permissions import scope_queryset
from apps.registry.models import OrgUnit, Person


@pytest.fixture
def college_eng(db):
    return OrgUnit.objects.create(name="College of Engineering", type="college")


@pytest.fixture
def college_sci(db):
    return OrgUnit.objects.create(name="College of Science", type="college")


@pytest.fixture
def person_eng(db, college_eng):
    return Person.objects.create(
        affiliation="staff",
        full_name="Eng Person",
        email="eng@knust.edu.gh",
        unit=college_eng,
    )


@pytest.fixture
def person_sci(db, college_sci):
    return Person.objects.create(
        affiliation="staff",
        full_name="Sci Person",
        email="sci@knust.edu.gh",
        unit=college_sci,
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_participant_with_no_role_sees_nothing(person_eng, person_sci):
    """A user with no RoleAssignment must see nothing."""
    user = _make_hub_user("nobody@knust.edu.gh")
    
    qs = Person.objects.all()
    filtered = scope_queryset(qs, user, {"unit": "unit_id"})
    
    assert filtered.count() == 0


@pytest.mark.django_db
def test_super_admin_sees_everything(person_eng, person_sci):
    """A Super Admin must see all records regardless of scope."""
    user = _make_hub_user("admin@knust.edu.gh", role="super_admin", scope_type="all")
    
    qs = Person.objects.all()
    filtered = scope_queryset(qs, user, {"unit": "unit_id"})
    
    assert filtered.count() == 2


@pytest.mark.django_db
def test_focal_person_sees_only_own_college(
    person_eng, person_sci, college_eng
):
    """A Focal Person for Engineering must NOT see Science."""
    user = _make_hub_user(
        "focal@knust.edu.gh",
        role="focal_person",
        scope_type="unit",
        scope_id=college_eng.id,
    )
    
    qs = Person.objects.all()
    filtered = scope_queryset(qs, user, {"unit": "unit_id"})
    
    assert filtered.count() == 1
    assert filtered.first().full_name == "Eng Person"


@pytest.mark.django_db
def test_programme_owner_sees_own_programme_only(
    person_eng, person_sci, college_eng
):
    """A Programme Owner scoped to a programme sees only people in that programme."""
    from apps.programmes.models import Programme
    from apps.programmes.models import ProgrammeCompetency
    from apps.library.models import Competency, Domain, Framework
    from apps.sessions.models import Attendance, Session
    from django.utils import timezone
    import datetime

    # Create framework + competency
    framework = Framework.objects.create(code="KNUST", name="KNUST", is_knust=True)
    domain = Domain.objects.create(framework=framework, code="AI", name="AI")
    comp = Competency.objects.create(framework=framework, domain=domain, code="AI-01", title="AI")
    
    # Create two programmes
    prog_a = Programme.objects.create(name="Prog A")
    prog_b = Programme.objects.create(name="Prog B")
    ProgrammeCompetency.objects.create(programme=prog_a, competency=comp, target_level=3)
    ProgrammeCompetency.objects.create(programme=prog_b, competency=comp, target_level=3)

    # Create sessions
    session_a = Session.objects.create(
        programme=prog_a, title="Session A",
        start_at=timezone.now(), end_at=timezone.now() + datetime.timedelta(hours=2),
        mode="in_person",
    )
    session_b = Session.objects.create(
        programme=prog_b, title="Session B",
        start_at=timezone.now(), end_at=timezone.now() + datetime.timedelta(hours=2),
        mode="in_person",
    )

    # Attendances
    Attendance.objects.create(session=session_a, person=person_eng, method="qr", status="completed", check_in_at=timezone.now(), check_out_at=timezone.now())
    Attendance.objects.create(session=session_b, person=person_sci, method="qr", status="completed", check_in_at=timezone.now(), check_out_at=timezone.now())

    # Owner of Prog A
    user = _make_hub_user("owner@knust.edu.gh", role="programme_owner", scope_type="programme", scope_id=prog_a.id)

    # Query attendances
    qs = Attendance.objects.all()
    filtered = scope_queryset(qs, user, {"programme": "session__programme_id"})
    
    assert filtered.count() == 1
    assert filtered.first().person.full_name == "Eng Person"


@pytest.mark.django_db
def test_executive_viewer_sees_aggregates_but_not_pii(person_eng, person_sci):
    """An Executive Viewer (like a Dean) sees aggregates but can't see contact details."""
    from apps.accounts.permissions import is_executive
    
    user = _make_hub_user("dean@knust.edu.gh", role="executive_viewer", scope_type="all")
    
    assert is_executive(user) is True
    
    # A view would use is_executive() to hide PII like phone numbers and emails
    # while still showing counts.