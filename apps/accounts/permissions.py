"""Permission helpers resolve a user's roles into query filters.

Every Hub view that reads scoped data MUST call one of these helpers
and filter its queryset accordingly. Never trust the frontend.
"""

from dataclasses import dataclass

from django.db.models import Q

from apps.accounts.models import RoleAssignment


@dataclass
class Scope:
    """One resolved permission for a user."""

    role: str
    scope_type: str
    scope_id: int | None

    def matches_unit(self, unit_id):
        return self.scope_type == "unit" and self.scope_id == unit_id

    def matches_programme(self, programme_id):
        return self.scope_type == "programme" and self.scope_id == programme_id


def get_user_scopes(user):
    """Return the list of scopes for the user's hub account."""
    if not user or not user.is_authenticated:
        return []
    try:
        account = user.hub_account
    except AttributeError:
        return []
    return [
        Scope(role=r.role, scope_type=r.scope_type, scope_id=r.scope_id)
        for r in RoleAssignment.objects.filter(user_account=account)
    ]


def is_super_admin(user):
    """Super admins bypass all scope filters."""
    return any(s.role == "super_admin" for s in get_user_scopes(user))


def is_library_curator(user):
    return any(s.role == "library_curator" for s in get_user_scopes(user))


def is_programme_owner(user, programme_id=None):
    scopes = get_user_scopes(user)
    if any(s.role == "programme_owner" and s.scope_type == "all" for s in scopes):
        return True
    if programme_id is None:
        return any(s.role == "programme_owner" for s in scopes)
    return any(
        s.role == "programme_owner" and s.matches_programme(programme_id)
        for s in scopes
    )


def is_focal_person_for(user, unit_id):
    return any(
        s.role == "focal_person" and s.matches_unit(unit_id)
        for s in get_user_scopes(user)
    )


def is_executive(user):
    return any(s.role == "executive_viewer" for s in get_user_scopes(user))


def scope_queryset(queryset, user, field_map):
    """Filter a queryset by the user's scopes.

    field_map maps scope_type → the queryset field name, e.g.
      {"unit": "unit_id", "programme": "programme_id"}
    Super admins and users with an `all` scope see everything.
    Users with no scopes see nothing.
    """
    scopes = get_user_scopes(user)
    if not scopes:
        return queryset.none()
    if any(s.role == "super_admin" or s.scope_type == "all" for s in scopes):
        return queryset

    q = Q(pk__in=[])  # start with "match nothing"
    for s in scopes:
        field = field_map.get(s.scope_type)
        if field and s.scope_id is not None:
            q |= Q(**{field: s.scope_id})
    return queryset.filter(q)


def can_view_person(user, person_id):
    """Can this user see another person's details?"""
    scopes = get_user_scopes(user)
    if any(s.role in ("super_admin", "executive_viewer") or s.scope_type == "all"
           for s in scopes):
        return True
    if any(s.role == "focal_person" for s in scopes):
        from apps.registry.models import Person
        person = Person.objects.filter(pk=person_id).first()
        if person and person.unit_id:
            return is_focal_person_for(user, person.unit_id)
    return False